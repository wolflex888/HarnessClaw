from __future__ import annotations

import re as _re
from pathlib import Path

import pytest

from harness_claw.gateway.redaction import RedactionConfig


def test_missing_file_returns_defaults():
    cfg = RedactionConfig.load(Path("/nonexistent/redaction.yaml"))
    assert cfg.enabled is True
    assert cfg.categories["anthropic_key"] is True
    assert cfg.categories["email"] is False
    assert cfg.custom_rules == []


def test_partial_file_merges_with_defaults(tmp_path: Path):
    path = tmp_path / "redaction.yaml"
    path.write_text("enabled: true\ncategories:\n  email: on\n")
    cfg = RedactionConfig.load(path)
    assert cfg.categories["email"] is True
    assert cfg.categories["anthropic_key"] is True  # default preserved


def test_unknown_category_logs_warning_but_loads(tmp_path: Path, caplog):
    path = tmp_path / "redaction.yaml"
    path.write_text("categories:\n  quantum_key: on\n")
    cfg = RedactionConfig.load(path)
    assert "quantum_key" not in cfg.categories
    assert any("quantum_key" in rec.getMessage() for rec in caplog.records)


def test_malformed_custom_rule_regex_raises_at_load(tmp_path: Path):
    path = tmp_path / "redaction.yaml"
    path.write_text(
        "custom_rules:\n"
        "  - name: bad\n"
        "    pattern: '[unclosed'\n"
        "    replacement: 'x'\n"
    )
    with pytest.raises(_re.error):
        RedactionConfig.load(path)


def test_custom_rule_default_replacement_uses_name(tmp_path: Path):
    path = tmp_path / "redaction.yaml"
    path.write_text(
        "custom_rules:\n"
        "  - name: acme_token\n"
        "    pattern: 'ACME-[A-Z0-9]{5}'\n"
    )
    cfg = RedactionConfig.load(path)
    assert cfg.custom_rules[0].replacement == "[REDACTED:acme_token]"


def test_enabled_false_is_structurally_valid(tmp_path: Path):
    path = tmp_path / "redaction.yaml"
    path.write_text("enabled: false\n")
    cfg = RedactionConfig.load(path)
    assert cfg.enabled is False
    # categories still populated with defaults for inspection
    assert "anthropic_key" in cfg.categories


def test_custom_rules_missing_name_raises_value_error(tmp_path: Path):
    path = tmp_path / "redaction.yaml"
    path.write_text(
        "custom_rules:\n"
        "  - pattern: 'ACME-[A-Z0-9]{5}'\n"
    )
    with pytest.raises(ValueError, match=r"custom_rules\[0\].*name"):
        RedactionConfig.load(path)
