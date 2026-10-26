import pytest

from demo.support import POLICY_PATH
from policyproxy.policy import Condition, PolicyError, PolicySet, Risk
from policyproxy.types import Verdict


@pytest.mark.parametrize(
    ("raw", "args", "expected"),
    [
        ({"arg": "amount", "lte": 500}, {"amount": 500}, True),
        ({"arg": "amount", "lte": 500}, {"amount": 500.01}, False),
        ({"arg": "amount", "gt": 0}, {"amount": True}, False),  # bools aren't numbers here
        ({"arg": "amount", "gt": 0}, {"amount": "10"}, False),
        ({"arg": "amount", "gt": 0}, {}, False),  # missing fails closed
        ({"arg": "limit", "lte": 50, "optional": True}, {}, True),
        ({"arg": "id", "regex": r"o_\d+"}, {"id": "o_12"}, True),
        ({"arg": "id", "regex": r"o_\d+"}, {"id": "o_12; drop"}, False),  # fullmatch, not search
        ({"arg": "type", "in": ["bug"]}, {"type": ["bug"]}, False),  # unhashable doesn't crash
        ({"arg": "body", "not_contains_any": ["Internal:"]}, {"body": "INTERNAL: x"}, False),
        ({"arg": "body", "contains_any": ["refund"]}, {"body": "Refunded!"}, True),
        ({"arg": "body", "max_len": 3}, {"body": "abcd"}, False),
        ({"arg": "x", "present": True}, {"x": None}, True),
        ({"arg": "x", "absent": True}, {"x": None}, False),
    ],
)
def test_condition(raw, args, expected):
    assert Condition.parse(raw).holds(args) is expected


def test_condition_needs_exactly_one_operator():
    with pytest.raises(PolicyError):
        Condition.parse({"arg": "a", "lt": 1, "gt": 0})
    with pytest.raises(PolicyError):
        Condition.parse({"arg": "a"})


def test_support_policies_load():
    policies = PolicySet.load(POLICY_PATH)
    assert policies.unmatched is Verdict.BLOCK
    assert [p.id for p in policies.matching("delete_customer")] == ["no-destructive-tools"]
    refund = policies.matching("refund")[0]
    assert refund.risk is Risk.MUTATE and refund.on_failure is Verdict.BLOCK
    assert refund.judge is not None and refund.judge.evidence == ("customer_messages",)
    assert policies.matching("get_customer_profile")[0].on_failure is Verdict.ALLOW  # reads default to allow


def test_version_tracks_file_contents(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text("policies: []\n")
    first = PolicySet.load(path).version
    path.write_text("policies: []\nunmatched: allow\n")
    assert PolicySet.load(path).version != first


def test_duplicate_ids_rejected():
    raw = {"policies": [{"id": "a", "tools": ["x"]}, {"id": "a", "tools": ["y"]}]}
    with pytest.raises(PolicyError):
        PolicySet.from_dict(raw)
