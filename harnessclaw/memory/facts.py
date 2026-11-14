"""A fact is one line of memory: a short, durable statement about the user."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

PERSONAL = "personal"
PROJECT_PREFIX = "project:"

# Facts written by hand (in the Markdown files or from the CLI) are recorded as
# written by "user". The tidy-up never merges or drops them.
USER = "user"

MAX_CONTENT = 500

_PROJECT_RE = re.compile(r"[^A-Za-z0-9._-]+")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_id() -> str:
    return "m_" + uuid.uuid4().hex[:8]


def project_scope(name: str | None) -> str:
    """`project:<name>` for a project, `personal` for none."""
    if not name:
        return PERSONAL
    clean = _PROJECT_RE.sub("-", name.strip()).strip("-.").lower()
    if not clean:
        return PERSONAL
    return PROJECT_PREFIX + clean


def clean_content(text: str) -> str:
    """One line, no surrounding space, no characters that would break the file format."""
    text = " ".join(text.split())
    text = text.replace("<!--", "<! --").replace("-->", "-- >")
    if len(text) > MAX_CONTENT:
        raise ValueError(f"a memory is one short fact (at most {MAX_CONTENT} characters)")
    return text


def clean_category(category: str | None) -> str:
    category = " ".join((category or "").split()).lower()
    return category or "general"


@dataclass
class Fact:
    id: str
    content: str
    category: str = "general"
    scope: str = PERSONAL
    private: bool = False
    by: str = USER  # the client that wrote it: "user", "claude-code", "tidy", ...
    model: str = ""  # the model that wrote it, when known
    at: str = ""  # when it was written or last changed

    @property
    def user_written(self) -> bool:
        return self.by == USER

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fact:
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})
