"""The tiered enforcement engine.

For each proposed tool call:
  T0  run every matching policy's deterministic rules; any failure blocks.
  T1  for policies whose judge should run, reuse a cached verdict if one exists.
  T2  otherwise ask the judge: on the request path for mutating tools, in the
      background for read-only tools (allowed now, flagged if the judge objects).
"""

from __future__ import annotations

import hashlib
import itertools
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass

from policyproxy.cache import DecisionCache
from policyproxy.evidence import Evidence
from policyproxy.judges import Judge
from policyproxy.policy import Policy, PolicySet, Risk
from policyproxy.types import Decision, Tier, ToolCall, Verdict, slowest


@dataclass(frozen=True)
class AuditFlag:
    """A read the background judge objected to after it was allowed."""

    audit_id: int
    policy_id: str
    call: ToolCall
    reason: str


@dataclass(frozen=True)
class _Outcome:
    allow: bool
    reason: str
    latency_ms: float
    failed: bool


class Engine:
    def __init__(
        self,
        policies: PolicySet,
        judge: Judge | None,
        cache: DecisionCache | None = None,
        judge_timeout_s: float = 1.5,
        max_workers: int = 16,
    ):
        self.policies = policies
        self.judge = judge  # None = rules only: escalations are skipped and allowed
        self.cache = cache if cache is not None else DecisionCache()
        self.judge_timeout_ms = judge_timeout_s * 1000
        self.flags: list[AuditFlag] = []
        self.audit_errors = 0
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="policy-judge")
        self._audits: list[Future] = []
        self._audit_ids = itertools.count(1)
        self._lock = threading.Lock()

    def evaluate(self, call: ToolCall, evidence: Evidence) -> Decision:
        start = time.perf_counter()
        tier = Tier.DETERMINISTIC
        judge_ms = 0.0  # judge latency as the judge reports it (simulated judges don't sleep)
        judge_wall_s = 0.0  # wall time spent waiting on the judge, excluded from engine overhead
        judge_calls = 0
        audit_ids: list[int] = []

        def done(verdict: Verdict, policy: Policy | None = None, reason: str = "", hint: str | None = None) -> Decision:
            overhead_ms = (time.perf_counter() - start - judge_wall_s) * 1000
            return Decision(
                verdict=verdict,
                tier=tier,
                latency_ms=overhead_ms + judge_ms,
                policy_id=policy.id if policy else None,
                reason=reason,
                hint=hint,
                judge_calls=judge_calls,
                audit_ids=tuple(audit_ids),
            )

        matched = self.policies.matching(call.tool)
        if not matched:
            if self.policies.unmatched is Verdict.BLOCK:
                return done(Verdict.BLOCK, reason=f"No policy covers tool {call.tool!r}.", hint="Only call tools this agent is configured to use.")
            return done(Verdict.ALLOW)

        # T0: deterministic rules for every matching policy before any judge runs.
        for policy in matched:
            if policy.deny:
                return done(Verdict.BLOCK, policy, policy.message, policy.hint)
            for rule in policy.rules:
                if not rule.holds(call.args):
                    return done(Verdict.BLOCK, policy, rule.message, rule.hint)

        if self.judge is None:
            return done(Verdict.ALLOW)

        for policy in matched:
            spec = policy.judge
            if spec is None or not spec.should_escalate(call.args):
                continue

            # T1: a verdict for this exact call against this exact evidence.
            key = self._cache_key(policy, call, evidence)
            cached = self.cache.get(key)
            if cached is not None:
                tier = slowest(tier, Tier.CACHE)
                if cached.verdict is Verdict.BLOCK:
                    return done(Verdict.BLOCK, policy, cached.reason, spec.hint)
                continue

            # T2, reads: don't make the agent wait; judge in the background.
            if policy.risk is Risk.READ:
                tier = slowest(tier, Tier.JUDGE_ASYNC)
                audit_ids.append(self._audit(policy, call, evidence, key))
                continue

            # T2, mutations: the judge decides before the action runs.
            waited = time.perf_counter()
            outcome = self._judge_sync(policy, call, evidence)
            judge_wall_s += time.perf_counter() - waited
            judge_ms += outcome.latency_ms
            judge_calls += 1
            if outcome.failed:
                tier = slowest(tier, Tier.JUDGE_FAILED)
                if policy.on_failure is Verdict.BLOCK:
                    return done(
                        Verdict.BLOCK,
                        policy,
                        f"Policy check unavailable ({outcome.reason}), so this action is blocked.",
                        "Retry shortly, or hand off to a human.",
                    )
                continue
            tier = slowest(tier, Tier.JUDGE)
            self.cache.put(key, Verdict.ALLOW if outcome.allow else Verdict.BLOCK, outcome.reason)
            if not outcome.allow:
                return done(Verdict.BLOCK, policy, outcome.reason, spec.hint)

        return done(Verdict.ALLOW)

    def drain(self, timeout_s: float | None = None) -> None:
        """Wait for background audits to finish (end of a run, or a benchmark)."""
        with self._lock:
            pending = list(self._audits)
        wait(pending, timeout=timeout_s)

    def close(self) -> None:
        self.drain()
        self._pool.shutdown(wait=True)

    def _cache_key(self, policy: Policy, call: ToolCall, evidence: Evidence) -> str:
        assert policy.judge is not None
        raw = "|".join((self.policies.version, policy.id, call.canonical(), evidence.fingerprint(policy.judge.evidence)))
        return hashlib.sha256(raw.encode()).hexdigest()

    def _judge_sync(self, policy: Policy, call: ToolCall, evidence: Evidence) -> _Outcome:
        assert self.judge is not None and policy.judge is not None
        view = evidence.view(policy.judge.evidence)
        started = time.perf_counter()
        future = self._pool.submit(self.judge.judge, policy, call, view)
        try:
            result = future.result(timeout=self.judge_timeout_ms / 1000)
        except FuturesTimeout:
            return _Outcome(False, "timeout", self.judge_timeout_ms, failed=True)
        except Exception as error:  # any judge failure falls back to the policy's on_failure
            return _Outcome(False, type(error).__name__, (time.perf_counter() - started) * 1000, failed=True)
        if result.latency_ms > self.judge_timeout_ms:  # a simulated judge reports latency instead of sleeping
            return _Outcome(False, "timeout", self.judge_timeout_ms, failed=True)
        return _Outcome(result.allow, result.reason, result.latency_ms, failed=False)

    def _audit(self, policy: Policy, call: ToolCall, evidence: Evidence, key: str) -> int:
        assert self.judge is not None and policy.judge is not None
        audit_id = next(self._audit_ids)
        view = evidence.view(policy.judge.evidence)

        def run() -> None:
            try:
                result = self.judge.judge(policy, call, view)
            except Exception:
                with self._lock:
                    self.audit_errors += 1
                return
            self.cache.put(key, Verdict.ALLOW if result.allow else Verdict.BLOCK, result.reason)
            if not result.allow:
                with self._lock:
                    self.flags.append(AuditFlag(audit_id, policy.id, call, result.reason))

        future = self._pool.submit(run)
        with self._lock:
            self._audits.append(future)
        return audit_id
