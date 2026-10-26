"""Scripted walkthrough of a support agent running behind the policy engine.

The agent's steps are scripted so the demo is the same every run; the policy
decisions, latencies and feedback are real engine output.

    python -m demo.run_demo                 # simulated judge, no API key
    python -m demo.run_demo --judge claude  # Claude as the tier-2 judge
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from demo.support import POLICY_PATH, FakeBackend, oracle
from policyproxy import Engine, Evidence, Guard, PolicySet, SimulatedJudge, Tier
from policyproxy.types import Decision, ToolCall

USE_COLOR = sys.stdout.isatty()


def paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


TIER_COLOR = {
    Tier.DETERMINISTIC: "36",
    Tier.CACHE: "34",
    Tier.JUDGE_ASYNC: "35",
    Tier.JUDGE: "33",
    Tier.JUDGE_FAILED: "31",
}


def trace(call: ToolCall, decision: Decision) -> None:
    tier = paint(f"{decision.tier.value:<9}", TIER_COLOR[decision.tier])
    verdict = paint("ALLOW", "32") if decision.allowed else paint("BLOCK", "1;31")
    print(f"  {tier} {decision.latency_ms:>8.2f} ms  {verdict}  {call}")
    if decision.allowed and decision.tier is Tier.JUDGE_ASYNC:
        print(paint("                               allowed now; judge running in the background", "2"))
    if not decision.allowed:
        feedback = json.dumps({k: v for k, v in decision.feedback().items() if k != "note" and v}, indent=None)
        print(paint(f"                               feedback -> {feedback}", "31"))


def agent(text: str) -> None:
    print(paint(f"\nagent: {text}", "1"))


def note(text: str) -> None:
    print(paint(f"  {text}", "2"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--judge", choices=["sim", "claude"], default="sim")
    args = parser.parse_args()

    if args.judge == "claude":
        from policyproxy import ClaudeJudge

        judge = ClaudeJudge()
        judge_label = f"Claude ({judge.model})"
    else:
        judge = SimulatedJudge(oracle, median_ms=400, sigma=0.35, sleep=True)
        judge_label = "simulated (oracle answers, ~400 ms LLM-like latency)"

    backend = FakeBackend()
    engine = Engine(PolicySet.load(POLICY_PATH), judge, judge_timeout_s=10 if args.judge == "claude" else 1.5)
    evidence = Evidence({"session_customer": ["c_42"]})
    guard = Guard(engine, backend.tools(), evidence, tracer=trace)

    print(paint("Tiered policy engine: customer-support agent demo", "1"))
    print(f"policies: {POLICY_PATH.name} (version {engine.policies.version})   judge: {judge_label}")

    message = "Hi, the TV from order o_4201 arrived with a cracked screen, and the wall mount (o_4202) never showed up."
    print(paint(f"\ncustomer c_42: {message}", "1;37"))
    evidence.record("customer_messages", message)

    agent("Look up the customer's orders and profile.")
    guard.call("search_orders", customer_id="c_42", limit=10)
    guard.call("get_customer_profile", customer_id="c_42")

    agent("Refund the TV in full.")
    result = guard.call("refund", order_id="o_4201", amount=899.0)
    if not result["ok"]:
        agent("Blocked over the $500 limit. Following the hint: open an approval ticket instead.")
        guard.call("create_ticket", type="refund_approval", summary="Customer c_42: TV (o_4201, $899) arrived cracked. Requesting full refund.")

    agent("Refund the missing wall mount ($150).")
    guard.call("refund", order_id="o_4202", amount=150.0)

    agent("Also refund the HDMI cable ($120) as a goodwill gesture.")
    result = guard.call("refund", order_id="o_4203", amount=120.0)
    if not result["ok"]:
        agent("The customer never reported a problem with o_4203. Dropping that refund.")

    agent("Email the customer an update.")
    result = guard.call(
        "send_email",
        to="c_42@customers.example.com",
        subject="Your order update",
        body="INTERNAL: frequent refunder, watch this account.\n\nWe've refunded the wall mount.",
    )
    if not result["ok"]:
        agent("Removing the internal note and resending.")
    body = (
        "We've issued a $150 refund for the wall mount (o_4202). A specialist is reviewing the TV, "
        "and you'll hear back within 2 business days."
    )
    backend.fail_next.add("send_email")
    result = guard.call("send_email", to="c_42@customers.example.com", subject="Your order update", body=body)
    if not result["ok"] and not result.get("blocked"):
        note(f"tool error: {result['error']}")
        agent("The mail server timed out. Retrying the same email.")
        guard.call("send_email", to="c_42@customers.example.com", subject="Your order update", body=body)
        note("same call + same evidence -> cached verdict, no second judge call")

    agent("Check whether c_77 is a related account.")
    guard.call("get_customer_profile", customer_id="c_77")
    engine.drain()
    for flag in engine.flags:
        print(paint(f"  audit flag #{flag.audit_id} [{flag.policy_id}] {flag.call}: {flag.reason}", "1;35"))
    agent("Look at c_77 again.")
    guard.call("get_customer_profile", customer_id="c_77")
    note("the background verdict is cached, so the repeat is blocked on the request path")

    agent("Close out by deleting the customer record.")
    guard.call("delete_customer", customer_id="c_42")

    engine.close()
    decisions = [decision for _, decision in guard.decisions]
    tiers = Counter(decision.tier.value for decision in decisions)
    judge_calls = sum(decision.judge_calls for decision in decisions)
    total_ms = sum(decision.latency_ms for decision in decisions)
    print(paint("\nsummary", "1"))
    print(f"  {len(decisions)} tool calls checked   by tier: {dict(sorted(tiers.items()))}")
    print(f"  judge calls on the request path: {judge_calls}   background audits flagged: {len(engine.flags)}")
    print(f"  time spent in policy checks: {total_ms:.0f} ms total")
    print(f"  refunds issued: {backend.refunds}")
    print(f"  emails sent: {len(backend.emails)}   tickets: {[t['id'] for t in backend.tickets]}")


if __name__ == "__main__":
    main()
