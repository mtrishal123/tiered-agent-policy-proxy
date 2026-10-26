from types import SimpleNamespace

import pytest

pytest.importorskip("anthropic")

from demo.support import POLICY_PATH
from policyproxy import ClaudeJudge, PolicySet, ToolCall
from policyproxy.judges import JudgeError


class StubMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def stub_client(stop_reason="end_turn", text='{"allow": false, "reason": "no complaint about o_9"}'):
    content = [SimpleNamespace(type="text", text=text)] if text is not None else []
    messages = StubMessages(SimpleNamespace(stop_reason=stop_reason, content=content))
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


REFUND = PolicySet.load(POLICY_PATH).matching("refund")[0]
CALL = ToolCall("refund", {"order_id": "o_9", "amount": 150})


def test_parses_structured_verdict_and_sends_policy_call_and_evidence():
    client, messages = stub_client()
    result = ClaudeJudge(client=client).judge(REFUND, CALL, {"customer_messages": ["hi"]})
    assert result.allow is False and "o_9" in result.reason
    sent = messages.kwargs
    assert sent["model"] == "claude-opus-5"
    assert sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]
    assert sent["output_config"]["format"]["type"] == "json_schema"
    prompt = sent["messages"][0]["content"]
    assert REFUND.judge.prompt in prompt and CALL.canonical() in prompt and '"hi"' in prompt


@pytest.mark.parametrize(("stop_reason", "text"), [("refusal", None), ("max_tokens", "{"), ("end_turn", None)])
def test_unusable_responses_raise(stop_reason, text):
    client, _ = stub_client(stop_reason, text)
    with pytest.raises(JudgeError):
        ClaudeJudge(client=client).judge(REFUND, CALL, {})
