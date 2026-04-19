from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)


def _default_categories() -> dict[str, bool]:
    return {
        "anthropic_key": True,
        "openai_key": True,
        "github_token": True,
        "aws_access_key": True,
        "aws_secret_key": True,
        "gcp_key": True,
        "slack_webhook": True,
        "jwt": True,
        "ssh_private_key": True,
        "email": False,
        "phone_us": False,
        "credit_card": False,
        "ssn_us": False,
        "user_path": False,
    }


@dataclass
class CustomRule:
    name: str
    pattern: str
    replacement: str | None = None

    def __post_init__(self) -> None:
        # Compile eagerly to fail fast on bad regex.
        self._compiled = re.compile(self.pattern)
        if self.replacement is None:
            self.replacement = f"[REDACTED:{self.name}]"

    @property
    def compiled(self) -> re.Pattern[str]:
        return self._compiled


@dataclass
class RedactionConfig:
    enabled: bool = True
    categories: dict[str, bool] = field(default_factory=_default_categories)
    custom_rules: list[CustomRule] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> "RedactionConfig":
        path = path or Path("redaction.yaml")
        if not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text()) or {}

        known = _default_categories()
        cats: dict[str, bool] = dict(known)
        for name, on in (data.get("categories") or {}).items():
            if name not in known:
                log.warning("redaction: unknown category %r in %s — ignoring", name, path)
                continue
            cats[name] = bool(on)

        rules: list[CustomRule] = []
        for entry in data.get("custom_rules") or []:
            rules.append(CustomRule(
                name=entry["name"],
                pattern=entry["pattern"],
                replacement=entry.get("replacement"),
            ))

        return cls(
            enabled=bool(data.get("enabled", True)),
            categories=cats,
            custom_rules=rules,
        )
