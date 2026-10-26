"""Compare three ways to enforce the same policies on the same synthetic traffic.

  llm-every-call  an LLM judge checks every call against the whole policy (the naive design)
  rules-only      tier 0 only: deterministic rules, no judge
  tiered          T0 rules -> T1 verdict cache -> T2 judge only when needed

Engine overhead (T0/T1) is measured for real. Judge latency is simulated
(log-normal, see --judge-median-ms), so runs are fast and reproducible; the
judge's answers come from an oracle with a --judge-error-rate chance of being
wrong. Swap in ClaudeJudge to measure a live judge.

    python -m bench.run_bench [--n 5000] [--seed 7]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from bench.dataset import Item, generate
from demo.support import POLICY_PATH, oracle
from policyproxy import Engine, Evidence, PolicySet, SimulatedJudge
from policyproxy.types import Tier

READ_TOOLS = {"search_orders", "get_customer_profile"}
RESULTS = Path(__file__).resolve().parent.parent / "results"


@dataclass
class Report:
    mode: str
    latencies: list[float] = field(default_factory=list)
    tiers: Counter = field(default_factory=Counter)
    order: list[str] = field(default_factory=list)  # deciding tier per call, aligned with latencies
    judge_calls_sync: int = 0
    judge_calls_total: int = 0
    prevented: int = 0  # violation blocked before it ran
    detected_after: int = 0  # violation allowed, then flagged by a background audit
    missed: int = 0
    false_blocks: int = 0
    violations: int = 0
    legit: int = 0

    def score(self, item: Item, blocked: bool, flagged: bool = False) -> None:
        if item.should_block:
            self.violations += 1
            if blocked:
                self.prevented += 1
            elif flagged:
                self.detected_after += 1
            else:
                self.missed += 1
        else:
            self.legit += 1
            self.false_blocks += blocked


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q / 100 * len(ordered)) - 1))]


def run_llm_every_call(items: list[Item], judge: SimulatedJudge, timeout_ms: float) -> Report:
    report = Report("llm-every-call")
    for item in items:
        latency_ms, errs = judge.draw(f"baseline|{item.call.canonical()}|{json.dumps(item.evidence, sort_keys=True)}")
        report.judge_calls_sync += 1
        if latency_ms > timeout_ms:
            report.latencies.append(timeout_ms)
            report.tiers["T2-failed"] += 1
            blocked = item.call.tool not in READ_TOOLS  # same fail-closed-for-mutations rule as tiered
        else:
            report.latencies.append(latency_ms)
            report.tiers["T2"] += 1
            blocked = item.should_block != errs
        report.score(item, blocked)
    report.judge_calls_total = report.judge_calls_sync
    return report


def run_engine(mode: str, items: list[Item], policies: PolicySet, judge: SimulatedJudge | None, timeout_ms: float) -> Report:
    report = Report(mode)
    engine = Engine(policies, judge, judge_timeout_s=timeout_ms / 1000)
    decisions = []
    for item in items:
        decision = engine.evaluate(item.call, Evidence(item.evidence))
        decisions.append(decision)
        report.latencies.append(decision.latency_ms)
        report.tiers[decision.tier.value] += 1
        report.order.append(decision.tier.value)
        report.judge_calls_sync += decision.judge_calls
    engine.close()
    flagged_ids = {flag.audit_id for flag in engine.flags}
    for item, decision in zip(items, decisions):
        report.score(item, not decision.allowed, flagged=bool(flagged_ids.intersection(decision.audit_ids)))
    report.judge_calls_total = report.judge_calls_sync + sum(1 for d in decisions if d.tier is Tier.JUDGE_ASYNC)
    return report


def ms(value: float) -> str:
    return f"{value:.3f}" if value < 1 else f"{value:.0f}"


def table(reports: list[Report], n: int, cost_per_judge_call: float) -> str:
    rows = [
        "| mode | p50 ms | p90 ms | p99 ms | mean ms | judge calls / 1k (request path) | judge cost / 1k actions | prevented | detected after | missed | false blocks |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in reports:
        rows.append(
            f"| {r.mode} | {ms(pct(r.latencies, 50))} | {ms(pct(r.latencies, 90))} | {ms(pct(r.latencies, 99))} "
            f"| {ms(sum(r.latencies) / n)} | {r.judge_calls_total * 1000 / n:.0f} ({r.judge_calls_sync * 1000 / n:.0f}) "
            f"| ${r.judge_calls_total * cost_per_judge_call * 1000 / n:.2f} "
            f"| {r.prevented}/{r.violations} ({r.prevented / r.violations:.1%}) | {r.detected_after} | {r.missed} "
            f"| {r.false_blocks}/{r.legit} ({r.false_blocks / r.legit:.2%}) |"
        )
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--judge-median-ms", type=float, default=400.0)
    parser.add_argument("--judge-error-rate", type=float, default=0.02)
    parser.add_argument("--judge-timeout-ms", type=float, default=1500.0)
    parser.add_argument(
        "--judge-cost-usd",
        type=float,
        default=0.0055,
        help="assumed cost per judge call: ~800 input + 60 output tokens at $5/$25 per MTok, thinking tokens not counted",
    )
    args = parser.parse_args()

    items = generate(args.n, args.seed)
    policies = PolicySet.load(POLICY_PATH)

    def judge() -> SimulatedJudge:
        return SimulatedJudge(oracle, median_ms=args.judge_median_ms, sigma=0.5, error_rate=args.judge_error_rate, sleep=False, seed=args.seed)

    reports = [
        run_llm_every_call(items, judge(), args.judge_timeout_ms),
        run_engine("rules-only", items, policies, None, args.judge_timeout_ms),
        run_engine("tiered", items, policies, judge(), args.judge_timeout_ms),
    ]

    kinds = Counter(item.kind for item in items)
    tiered = reports[-1]
    fast = sum(1 for latency in tiered.latencies if latency < 5) / args.n
    fast_path = [latency for latency, tier in zip(tiered.latencies, tiered.order) if tier in ("T0", "T1", "T2-async")]
    summary = "\n".join(
        [
            f"# Benchmark: {args.n} synthetic support-agent tool calls (seed {args.seed})",
            "",
            f"Traffic: {dict(kinds)}; {sum(i.should_block for i in items)} calls should be blocked.",
            f"Judge: simulated, median {args.judge_median_ms:.0f} ms, {args.judge_error_rate:.0%} error rate, "
            f"{args.judge_timeout_ms:.0f} ms timeout, ${args.judge_cost_usd} per call (assumed).",
            "",
            table(reports, args.n, args.judge_cost_usd),
            "",
            f"Tiered, by deciding tier: {dict(sorted(tiered.tiers.items()))}",
            f"Tiered, calls decided in under 5 ms: {fast:.1%}",
            f"Tiered, measured engine overhead on calls that never wait for a judge (T0/T1/T2-async, n={len(fast_path)}): "
            f"p50 {pct(fast_path, 50) * 1000:.0f} µs, p99 {pct(fast_path, 99) * 1000:.0f} µs",
            "",
            "`detected after` = read-only calls allowed immediately and flagged by the background judge.",
            "`judge calls / 1k` counts background audits; the number in parentheses is request-path calls only.",
            "Engine overhead (T0/T1) is measured; judge latency, accuracy and cost are simulated assumptions.",
        ]
    )
    print(summary)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "bench.md").write_text(summary + "\n")
    print(f"\nwrote {RESULTS / 'bench.md'}")


if __name__ == "__main__":
    main()
