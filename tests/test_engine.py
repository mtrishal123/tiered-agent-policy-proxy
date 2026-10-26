import time

import pytest

from demo.support import POLICY_PATH, FakeBackend, oracle
from policyproxy import DecisionCache, Engine, Evidence, Guard, JudgeResult, PolicySet, SimulatedJudge, Tier, ToolCall


class CountingJudge:
    """Oracle answers, instant, counts calls."""

    def __init__(self, raises: Exception | None = None, latency_ms: float = 1.0):
        self.calls = 0
        self.raises = raises
        self.latency_ms = latency_ms

    def judge(self, policy, call, evidence):
        self.calls += 1
        if self.raises:
            raise self.raises
        allow, reason = oracle(policy, call, evidence)
        return JudgeResult(allow, reason, self.latency_ms)


@pytest.fixture
def policies():
    return PolicySet.load(POLICY_PATH)


def session(*messages):
    return Evidence({"session_customer": ["c_42"], "customer_messages": list(messages)})


def test_rules_block_without_calling_judge(policies):
    judge = CountingJudge()
    engine = Engine(policies, judge)
    decision = engine.evaluate(ToolCall("refund", {"order_id": "o_1", "amount": 900}), session())
    assert not decision.allowed and decision.tier is Tier.DETERMINISTIC
    assert decision.policy_id == "refund-limits" and "create_ticket" in decision.hint
    assert judge.calls == 0


def test_unmatched_and_denied_tools_block(policies):
    engine = Engine(policies, CountingJudge())
    assert engine.evaluate(ToolCall("export_all", {}), session()).reason.startswith("No policy covers")
    assert engine.evaluate(ToolCall("delete_customer", {"customer_id": "c_42"}), session()).policy_id == "no-destructive-tools"


def test_judge_only_runs_inside_its_when_band(policies):
    judge = CountingJudge()
    engine = Engine(policies, judge)
    small = engine.evaluate(ToolCall("refund", {"order_id": "o_9", "amount": 50}), session())
    assert small.allowed and small.tier is Tier.DETERMINISTIC and judge.calls == 0

    large = engine.evaluate(ToolCall("refund", {"order_id": "o_9", "amount": 150}), session())
    assert not large.allowed and large.tier is Tier.JUDGE and judge.calls == 1


def test_cache_reuses_verdict_for_same_call_and_evidence(policies):
    judge = CountingJudge()
    engine = Engine(policies, judge)
    call = ToolCall("refund", {"order_id": "o_9", "amount": 150})
    evidence = session("My order o_9 is broken.")
    assert engine.evaluate(call, evidence).tier is Tier.JUDGE
    second = engine.evaluate(call, evidence)
    assert second.allowed and second.tier is Tier.CACHE and judge.calls == 1


def test_cache_misses_when_judged_evidence_changes(policies):
    judge = CountingJudge()
    engine = Engine(policies, judge)
    call = ToolCall("refund", {"order_id": "o_9", "amount": 150})
    evidence = session()
    assert not engine.evaluate(call, evidence).allowed
    evidence.record("customer_messages", "Actually o_9 arrived broken.")
    after = engine.evaluate(call, evidence)
    assert after.allowed and after.tier is Tier.JUDGE and judge.calls == 2


def test_cache_ignores_evidence_the_judge_does_not_read(policies):
    judge = CountingJudge()
    engine = Engine(policies, judge)
    call = ToolCall("refund", {"order_id": "o_9", "amount": 150})
    evidence = session("o_9 is broken")
    engine.evaluate(call, evidence)
    evidence.record("search_orders", [{"order_id": "o_9"}])
    assert engine.evaluate(call, evidence).tier is Tier.CACHE


def test_cache_entries_expire():
    now = [0.0]
    cache = DecisionCache(ttl_s=10, clock=lambda: now[0])
    from policyproxy.types import Verdict

    cache.put("k", Verdict.ALLOW, "ok")
    assert cache.get("k") is not None
    now[0] = 10.0
    assert cache.get("k") is None


def test_judge_failure_fails_closed_for_mutations(policies):
    engine = Engine(policies, CountingJudge(raises=RuntimeError("down")))
    decision = engine.evaluate(ToolCall("refund", {"order_id": "o_9", "amount": 150}), session("o_9 broken"))
    assert not decision.allowed and decision.tier is Tier.JUDGE_FAILED
    assert "unavailable" in decision.reason


def test_slow_judge_times_out(policies):
    engine = Engine(policies, SimulatedJudge(oracle, median_ms=400, sigma=0, sleep=True), judge_timeout_s=0.05)
    started = time.perf_counter()
    decision = engine.evaluate(ToolCall("refund", {"order_id": "o_9", "amount": 150}), session("o_9 broken"))
    assert time.perf_counter() - started < 0.3
    assert decision.tier is Tier.JUDGE_FAILED and not decision.allowed
    assert decision.latency_ms == pytest.approx(50, abs=5)
    engine.close()


def test_simulated_latency_over_timeout_counts_as_timeout(policies):
    engine = Engine(policies, CountingJudge(latency_ms=5000), judge_timeout_s=1.5)
    decision = engine.evaluate(ToolCall("refund", {"order_id": "o_9", "amount": 150}), session("o_9 broken"))
    assert decision.tier is Tier.JUDGE_FAILED and decision.latency_ms == pytest.approx(1500, abs=1)


def test_failed_judgments_are_not_cached(policies):
    judge = CountingJudge(raises=RuntimeError("down"))
    engine = Engine(policies, judge)
    call = ToolCall("refund", {"order_id": "o_9", "amount": 150})
    engine.evaluate(call, session("o_9 broken"))
    engine.evaluate(call, session("o_9 broken"))
    assert judge.calls == 2


def test_reads_are_allowed_then_audited(policies):
    judge = CountingJudge()
    engine = Engine(policies, judge)
    call = ToolCall("get_customer_profile", {"customer_id": "c_77"})
    first = engine.evaluate(call, session())
    assert first.allowed and first.tier is Tier.JUDGE_ASYNC and first.judge_calls == 0
    engine.drain()
    assert [flag.audit_id for flag in engine.flags] == list(first.audit_ids)

    repeat = engine.evaluate(call, session())
    assert not repeat.allowed and repeat.tier is Tier.CACHE


def test_rules_only_mode_never_judges(policies):
    engine = Engine(policies, None)
    decision = engine.evaluate(ToolCall("refund", {"order_id": "o_9", "amount": 150}), session())
    assert decision.allowed and decision.tier is Tier.DETERMINISTIC


def test_guard_returns_feedback_and_records_evidence(policies):
    backend = FakeBackend()
    guard = Guard(Engine(policies, CountingJudge()), backend.tools(), session("o_4202 never arrived"))
    blocked = guard.call("refund", order_id="o_4202", amount=900)
    assert blocked["ok"] is False and blocked["blocked"] is True and blocked["hint"]

    assert guard.call("refund", order_id="o_4202", amount=150)["ok"]
    assert backend.refunds == [{"order_id": "o_4202", "amount": 150}]

    promise = "We will refund your other order too."
    assert guard.call("send_email", to="c_42@customers.example.com", subject="s", body=promise)["ok"]  # refund on record


def test_guard_surfaces_tool_errors_without_blocking(policies):
    backend = FakeBackend()
    backend.fail_next.add("send_email")
    guard = Guard(Engine(policies, CountingJudge()), backend.tools(), session())
    result = guard.call("send_email", to="c_42@customers.example.com", subject="s", body="hello")
    assert result["ok"] is False and result["blocked"] is False and "timed out" in result["error"]
