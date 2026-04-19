from __future__ import annotations

import pytest

from harness_claw.gateway.redaction import (
    CustomRule,
    RedactionConfig,
    RegexRedactor,
)


def make_redactor(**cat_overrides) -> RegexRedactor:
    cfg = RedactionConfig()
    cfg.categories.update(cat_overrides)
    return RegexRedactor(cfg)


def test_anthropic_key_is_redacted():
    r = make_redactor()
    out = r.redact("token: sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-")
    assert "sk-ant-" not in out
    assert "[REDACTED:anthropic_key]" in out


def test_openai_key_is_redacted():
    r = make_redactor()
    out = r.redact("key: sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
    assert "[REDACTED:openai_key]" in out


def test_anthropic_matched_before_openai():
    r = make_redactor()
    # An sk-ant- key must be labeled as anthropic_key, not openai_key.
    out = r.redact("sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
    assert "[REDACTED:anthropic_key]" in out
    assert "openai_key" not in out


def test_github_token_redacted():
    r = make_redactor()
    out = r.redact("gh: ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
    assert "[REDACTED:github_token]" in out


def test_aws_access_key_redacted():
    r = make_redactor()
    out = r.redact("AKIAIOSFODNN7EXAMPLE in config")
    assert "[REDACTED:aws_access_key]" in out


def test_slack_webhook_redacted():
    r = make_redactor()
    out = r.redact("notify: https://hooks.slack.com/services/T00/B00/XXXXXXXXX")
    assert "[REDACTED:slack_webhook]" in out


def test_jwt_redacted():
    r = make_redactor()
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.sig_value_here_longer"
    out = r.redact(f"bearer {jwt}")
    assert "[REDACTED:jwt]" in out


def test_ssh_private_key_redacted():
    r = make_redactor()
    key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAA=\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    out = r.redact(key)
    assert "[REDACTED:ssh_private_key]" in out
    assert "PRIVATE KEY" not in out


def test_email_off_by_default():
    r = make_redactor()
    out = r.redact("contact me at alice@example.com")
    assert "alice@example.com" in out  # default is off


def test_email_on_when_enabled():
    r = make_redactor(email=True)
    out = r.redact("contact me at alice@example.com")
    assert "[REDACTED:email]" in out


def test_passes_through_ordinary_strings():
    r = make_redactor()
    out = r.redact("nothing sensitive here, just a normal log line")
    assert out == "nothing sensitive here, just a normal log line"


def test_recurses_into_dicts_and_lists():
    r = make_redactor()
    payload = {
        "outer": "ok",
        "inner": {"token": "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-"},
        "items": ["harmless", "gh: ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"],
    }
    out = r.redact(payload)
    assert out["outer"] == "ok"
    assert "[REDACTED:anthropic_key]" in out["inner"]["token"]
    assert "[REDACTED:github_token]" in out["items"][1]


def test_non_string_leaves_untouched():
    r = make_redactor()
    assert r.redact(42) == 42
    assert r.redact(3.14) == 3.14
    assert r.redact(True) is True
    assert r.redact(None) is None


def test_custom_rule_fires():
    cfg = RedactionConfig()
    cfg.custom_rules.append(CustomRule(name="acme", pattern=r"ACME-[A-Z0-9]{5}"))
    r = RegexRedactor(cfg)
    out = r.redact("key: ACME-AB123 value")
    assert "[REDACTED:acme]" in out


def test_idempotent():
    r = make_redactor()
    payload = {"k": "gh: ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"}
    once = r.redact(payload)
    twice = r.redact(once)
    assert once == twice
