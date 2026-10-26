"""Agent-facing wrapper: every tool call goes through the engine before it runs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from policyproxy.engine import Engine
from policyproxy.evidence import Evidence
from policyproxy.types import Decision, ToolCall

Tracer = Callable[[ToolCall, Decision], None]


class Guard:
    def __init__(self, engine: Engine, tools: dict[str, Callable[..., Any]], evidence: Evidence | None = None, tracer: Tracer | None = None):
        self.engine = engine
        self.tools = tools
        self.evidence = evidence or Evidence()
        self.tracer = tracer
        self.decisions: list[tuple[ToolCall, Decision]] = []

    def call(self, tool: str, **args: Any) -> dict[str, Any]:
        """Returns {"ok": True, "result": ...}, or the structured feedback the agent can recover from."""
        call = ToolCall(tool, args)
        decision = self.engine.evaluate(call, self.evidence)
        self.decisions.append((call, decision))
        if self.tracer:
            self.tracer(call, decision)
        if not decision.allowed:
            return {"ok": False, **decision.feedback()}
        try:
            result = self.tools[tool](**args)
        except Exception as error:
            return {"ok": False, "blocked": False, "error": f"{type(error).__name__}: {error}"}
        self.evidence.record(tool, result)
        return {"ok": True, "result": result}
