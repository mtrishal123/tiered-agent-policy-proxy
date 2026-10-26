"""Core value types shared by the engine, judges and guard."""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from typing import Any


class Verdict(str, enum.Enum):
    ALLOW = "allow"
    BLOCK = "block"


class Tier(str, enum.Enum):
    """Which part of the engine decided a call, ordered cheapest to most expensive."""

    DETERMINISTIC = "T0"  # compiled rules, no I/O
    CACHE = "T1"  # reused an earlier judge verdict
    JUDGE_ASYNC = "T2-async"  # allowed now, judged in the background (reads only)
    JUDGE = "T2"  # LLM judge on the request path
    JUDGE_FAILED = "T2-failed"  # judge timed out or errored; policy's on_failure applied


TIER_RANK = {tier: rank for rank, tier in enumerate(Tier)}


def slowest(a: Tier, b: Tier) -> Tier:
    return a if TIER_RANK[a] >= TIER_RANK[b] else b


@dataclass(frozen=True)
class ToolCall:
    tool: str
    args: dict[str, Any]

    def canonical(self) -> str:
        """Stable serialization: same tool + same args -> same string, regardless of key order."""
        return json.dumps(
            {"tool": self.tool, "args": self.args},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    def __str__(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in self.args.items())
        return f"{self.tool}({args})"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    tier: Tier
    latency_ms: float
    policy_id: str | None = None
    reason: str = ""
    hint: str | None = None
    judge_calls: int = 0
    audit_ids: tuple[int, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW

    def feedback(self) -> dict[str, Any]:
        """What the agent sees when a call is blocked, shaped so it can recover."""
        return {
            "blocked": True,
            "policy": self.policy_id,
            "reason": self.reason,
            "hint": self.hint,
            "note": "Repeating this exact call will be blocked again. Change the arguments or take a different action.",
        }
