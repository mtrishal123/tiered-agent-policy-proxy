import pytest

from demo import delivery
from demo.delivery import SmtpMailer, policies_allowing
from demo.support import FakeBackend, oracle
from policyproxy import Engine, Evidence, Guard, SimulatedJudge, ToolCall

ADDRESS = "someone+demo@gmail.com"


def email(to):
    return ToolCall("send_email", {"to": to, "subject": "s", "body": "hello"})


def test_widened_policy_allows_only_that_address():
    engine = Engine(policies_allowing(ADDRESS), None)
    evidence = Evidence()
    assert engine.evaluate(email(ADDRESS), evidence).allowed
    assert engine.evaluate(email("c_42@customers.example.com"), evidence).allowed
    assert not engine.evaluate(email("other@gmail.com"), evidence).allowed
    assert not engine.evaluate(email("someonexdemo@gmail.com"), evidence).allowed  # the address is escaped, not a pattern
    assert engine.policies.version.endswith("+deliver")


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        self.user = user

    def send_message(self, message):
        FakeSMTP.sent.append((message["From"], message["To"], message["Subject"], message.get_content()))


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(delivery.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def test_mailer_refuses_other_recipients(fake_smtp):
    mailer = SmtpMailer(ADDRESS, "me@gmail.com", "app-password")
    with pytest.raises(PermissionError):
        mailer.send("other@gmail.com", "s", "b")
    mailer.send(ADDRESS.upper(), "s", "b")
    assert fake_smtp.sent == [("me@gmail.com", ADDRESS.upper(), "s", "b\n")]


def test_from_env_requires_credentials(monkeypatch):
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="SMTP_USER and SMTP_PASSWORD"):
        SmtpMailer.from_env(ADDRESS)


def test_backend_delivers_through_guard(fake_smtp):
    backend = FakeBackend()
    backend.profile_email = ADDRESS
    backend.mailer = SmtpMailer(ADDRESS, "me@gmail.com", "app-password")
    guard = Guard(Engine(policies_allowing(ADDRESS), SimulatedJudge(oracle, sleep=False)), backend.tools(), Evidence({"session_customer": ["c_42"]}))

    assert guard.call("get_customer_profile", customer_id="c_42")["result"]["email"] == ADDRESS
    assert guard.call("send_email", to=ADDRESS, subject="Update", body="Your order shipped.")["ok"]
    assert guard.call("send_email", to="other@gmail.com", subject="x", body="y")["blocked"]
    assert [sent[1] for sent in fake_smtp.sent] == [ADDRESS]
