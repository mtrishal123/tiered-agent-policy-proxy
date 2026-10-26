"""Terminal output shared by the demos."""

from __future__ import annotations

import json
import sys

from policyproxy import Tier
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
