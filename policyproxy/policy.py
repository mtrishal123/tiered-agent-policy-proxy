"""Load YAML policies into objects the engine can evaluate without any I/O.

A policy has up to three parts:
  - rules: deterministic conditions every call must satisfy (tier 0)
  - judge: a plain-English check for what rules can't express, escalated to an
    LLM only when its `when` conditions hold (tier 2), cached by tier 1
  - deny: block the tool outright
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from policyproxy.types import Verdict


class PolicyError(ValueError):
    pass


class Risk(str, Enum):
    READ = "read"  # judged asynchronously: allowed now, flagged later
    MUTATE = "mutate"  # judged on the request path before the action runs


_MISSING = object()
_NUMERIC_OPS = {"lt", "lte", "gt", "gte"}
_OPS = _NUMERIC_OPS | {
    "eq",
    "regex",
    "in",
    "not_in",
    "contains_any",
    "not_contains_any",
    "max_len",
    "present",
    "absent",
}


@dataclass(frozen=True)
class Condition:
    """One check on one argument. Missing arguments fail unless `optional: true`."""

    arg: str
    op: str
    value: Any
    optional: bool = False

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Condition:
        if "arg" not in raw:
            raise PolicyError(f"condition is missing 'arg': {raw}")
        ops = [key for key in raw if key in _OPS]
        if len(ops) != 1:
            raise PolicyError(f"condition on {raw['arg']!r} needs exactly one operator, got {ops or 'none'}")
        op = ops[0]
        value = raw[op]
        if op == "regex":
            value = re.compile(value)
        elif op in ("contains_any", "not_contains_any"):
            value = tuple(str(v).lower() for v in value)
        elif op in ("in", "not_in"):
            value = frozenset(value)
        return cls(arg=raw["arg"], op=op, value=value, optional=bool(raw.get("optional", False)))

    def holds(self, args: dict[str, Any]) -> bool:
        actual = args.get(self.arg, _MISSING)
        if self.op == "present":
            return actual is not _MISSING
        if self.op == "absent":
            return actual is _MISSING
        if actual is _MISSING:
            return self.optional

        if self.op in _NUMERIC_OPS:
            if isinstance(actual, bool) or not isinstance(actual, (int, float)):
                return False
            if self.op == "lt":
                return actual < self.value
            if self.op == "lte":
                return actual <= self.value
            if self.op == "gt":
                return actual > self.value
            return actual >= self.value
        if self.op == "eq":
            return actual == self.value
        if self.op == "regex":
            return isinstance(actual, str) and self.value.fullmatch(actual) is not None
        if self.op in ("in", "not_in"):
            try:
                member = actual in self.value
            except TypeError:  # unhashable value, e.g. a list
                return False
            return member if self.op == "in" else not member
        if self.op in ("contains_any", "not_contains_any"):
            if not isinstance(actual, str):
                return False
            lowered = actual.lower()
            found = any(needle in lowered for needle in self.value)
            return found if self.op == "contains_any" else not found
        if self.op == "max_len":
            return isinstance(actual, (str, list, tuple)) and len(actual) <= self.value
        raise AssertionError(f"unhandled operator {self.op}")

    def describe(self) -> str:
        value = self.value.pattern if isinstance(self.value, re.Pattern) else self.value
        return f"{self.arg} {self.op} {value!r}"


@dataclass(frozen=True)
class Rule:
    condition: Condition
    message: str
    hint: str | None = None

    def holds(self, args: dict[str, Any]) -> bool:
        return self.condition.holds(args)


@dataclass(frozen=True)
class JudgeSpec:
    prompt: str
    when: tuple[Condition, ...] = ()  # all must hold to escalate; empty = always
    evidence: tuple[str, ...] = ()  # evidence keys the judge reads; also part of the cache key
    hint: str | None = None

    def should_escalate(self, args: dict[str, Any]) -> bool:
        return all(condition.holds(args) for condition in self.when)


@dataclass(frozen=True)
class Policy:
    id: str
    tools: tuple[str, ...]
    risk: Risk
    rules: tuple[Rule, ...] = ()
    judge: JudgeSpec | None = None
    deny: bool = False
    message: str = ""
    hint: str | None = None
    on_failure: Verdict = Verdict.BLOCK  # applied when a request-path judge times out or errors

    def matches(self, tool: str) -> bool:
        return any(fnmatch.fnmatchcase(tool, pattern) for pattern in self.tools)


class PolicySet:
    def __init__(self, policies: list[Policy], version: str, unmatched: Verdict = Verdict.BLOCK):
        ids = [policy.id for policy in policies]
        duplicates = {pid for pid in ids if ids.count(pid) > 1}
        if duplicates:
            raise PolicyError(f"duplicate policy ids: {sorted(duplicates)}")
        self.policies = tuple(policies)
        self.version = version
        self.unmatched = unmatched
        self.matching = lru_cache(maxsize=1024)(self._matching)

    def _matching(self, tool: str) -> tuple[Policy, ...]:
        return tuple(policy for policy in self.policies if policy.matches(tool))

    @classmethod
    def load(cls, path: str | Path) -> PolicySet:
        text = Path(path).read_text()
        return cls.from_dict(yaml.safe_load(text), version=hashlib.sha256(text.encode()).hexdigest()[:12])

    @classmethod
    def from_dict(cls, data: dict[str, Any], version: str = "inline") -> PolicySet:
        unmatched = Verdict(data.get("unmatched", "block"))
        return cls([_parse_policy(raw) for raw in data.get("policies", [])], version=version, unmatched=unmatched)


def _parse_policy(raw: dict[str, Any]) -> Policy:
    try:
        policy_id = raw["id"]
        tools = raw["tools"]
    except KeyError as missing:
        raise PolicyError(f"policy is missing {missing}: {raw}") from None
    risk = Risk(raw.get("risk", "mutate"))

    rules = []
    for rule in raw.get("rules", []):
        condition = Condition.parse(rule)
        rules.append(Rule(condition, rule.get("message") or f"Failed check: {condition.describe()}.", rule.get("hint")))

    judge = None
    if "judge" in raw:
        spec = raw["judge"]
        judge = JudgeSpec(
            prompt=spec["prompt"],
            when=tuple(Condition.parse(c) for c in spec.get("when", [])),
            evidence=tuple(spec.get("evidence", [])),
            hint=spec.get("hint"),
        )

    return Policy(
        id=policy_id,
        tools=tuple([tools] if isinstance(tools, str) else tools),
        risk=risk,
        rules=tuple(rules),
        judge=judge,
        deny=bool(raw.get("deny", False)),
        message=raw.get("message", f"Tool blocked by policy {policy_id}."),
        hint=raw.get("hint"),
        on_failure=Verdict(raw.get("on_failure", "block" if risk is Risk.MUTATE else "allow")),
    )
