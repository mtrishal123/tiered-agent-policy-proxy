"""Opt-in real email delivery for the live agent demo (`--deliver-to`).

Off by default. When on, exactly one extra address is allowed: the policy's
recipient rule is widened to that address only, and the mailer refuses any
other recipient even if a policy were to allow it.
"""

from __future__ import annotations

import os
import re
import smtplib
from email.message import EmailMessage
from typing import Any

import yaml

from demo.support import POLICY_PATH
from policyproxy import PolicySet


class SmtpMailer:
    """Sends through an SMTP server with STARTTLS. Defaults suit Gmail with an app password."""

    def __init__(self, allowed_to: str, user: str, password: str, host: str = "smtp.gmail.com", port: int = 587):
        self.allowed_to = allowed_to
        self.user = user
        self.password = password
        self.host = host
        self.port = port

    @classmethod
    def from_env(cls, allowed_to: str) -> SmtpMailer:
        missing = [name for name in ("SMTP_USER", "SMTP_PASSWORD") if not os.environ.get(name)]
        if missing:
            raise RuntimeError(f"--deliver-to needs {' and '.join(missing)} set (a Gmail address and its app password)")
        return cls(
            allowed_to,
            os.environ["SMTP_USER"],
            os.environ["SMTP_PASSWORD"],
            os.environ.get("SMTP_HOST", "smtp.gmail.com"),
            int(os.environ.get("SMTP_PORT", "587")),
        )

    def send(self, to: str, subject: str, body: str) -> None:
        if to.lower() != self.allowed_to.lower():
            raise PermissionError(f"real delivery is limited to {self.allowed_to}")
        message = EmailMessage()
        message["From"] = self.user
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(self.user, self.password)
            smtp.send_message(message)


def policies_allowing(address: str) -> PolicySet:
    """The support policies with the email recipient rule widened to also accept `address`."""
    text = POLICY_PATH.read_text()
    data: dict[str, Any] = yaml.safe_load(text)
    for policy in data["policies"]:
        if policy["id"] != "email-safety":
            continue
        for rule in policy["rules"]:
            if rule.get("arg") == "to" and "regex" in rule:
                rule["regex"] = f"{re.escape(address)}|{rule['regex']}"
                break
        else:
            raise ValueError("email-safety policy has no recipient regex to widen")
    base = PolicySet.load(POLICY_PATH).version
    return PolicySet.from_dict(data, version=f"{base}+deliver")
