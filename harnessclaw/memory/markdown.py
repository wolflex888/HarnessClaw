"""The Markdown memory files: what you read and edit by hand.

    # Personal memory

    <!-- intro comment -->

    ## preference

    - Prefers window seats <!-- id=m_3f2a1c9b by=claude-code model=claude-opus-5-5 at=2026-11-09T10:22:00Z -->

Each `- ` line is one fact, grouped under its category heading. The comment at
the end is bookkeeping. A line without one was added by hand: it gets an id
the next time the file is read, and is recorded as written by "user".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from harnessclaw.memory.facts import (
    PERSONAL,
    PROJECT_PREFIX,
    USER,
    Fact,
    clean_category,
    new_id,
    now,
)

_FACT_RE = re.compile(r"^\s*[-*]\s+(.*?)\s*$")
_META_RE = re.compile(r"\s*<!--(.*?)-->\s*$")
_HEADING_RE = re.compile(r"^\s*##\s+(.+?)\s*$")
_ID_RE = re.compile(r"^m_[0-9a-f]{8}$")

INTRO = (
    "<!-- One fact per line. Delete a line to forget it; add a line to remember it.\n"
    "     The note at the end of each line records who wrote it and when. -->"
)


def scope_path(root: Path, scope: str, private: bool) -> Path:
    suffix = ".private.md" if private else ".md"
    if scope == PERSONAL:
        return root / f"personal{suffix}"
    if scope.startswith(PROJECT_PREFIX):
        return root / "projects" / f"{scope[len(PROJECT_PREFIX):]}{suffix}"
    raise ValueError(f"unknown scope {scope!r}")


def path_scope(root: Path, path: Path) -> tuple[str, bool]:
    """The (scope, private) a memory file holds, from its path."""
    rel = path.relative_to(root)
    name = rel.name
    private = name.endswith(".private.md")
    stem = name[: -len(".private.md")] if private else name[: -len(".md")]
    if rel.parent == Path("."):
        if stem != "personal":
            raise ValueError(f"not a memory file: {path}")
        return PERSONAL, private
    if rel.parent == Path("projects"):
        return PROJECT_PREFIX + stem, private
    raise ValueError(f"not a memory file: {path}")


def title(scope: str, private: bool) -> str:
    base = "Personal memory" if scope == PERSONAL else f"Project memory: {scope[len(PROJECT_PREFIX):]}"
    return f"{base} (private)" if private else base


@dataclass
class ParsedFile:
    facts: list[Fact] = field(default_factory=list)
    # True when the file needs rewriting (lines without ids, duplicate ids).
    needs_ids: bool = False


def parse(text: str, scope: str, private: bool) -> ParsedFile:
    out = ParsedFile()
    seen: set[str] = set()
    category = "general"
    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            category = clean_category(heading.group(1))
            continue
        m = _FACT_RE.match(line)
        if not m:
            continue
        body = m.group(1)
        meta: dict[str, str] = {}
        mm = _META_RE.search(body)
        if mm:
            meta = _parse_meta(mm.group(1))
            body = body[: mm.start()]
        content = " ".join(body.split())
        if not content:
            continue
        fid = meta.get("id", "")
        if not _ID_RE.match(fid) or fid in seen:
            out.needs_ids = True
            out.facts.append(
                Fact(id=new_id(), content=content, category=category, scope=scope,
                     private=private, by=USER, at=now())
            )
            seen.add(out.facts[-1].id)
            continue
        seen.add(fid)
        out.facts.append(
            Fact(
                id=fid,
                content=content,
                category=category,
                scope=scope,
                private=private,
                by=meta.get("by", USER),
                model=meta.get("model", ""),
                at=meta.get("at", ""),
            )
        )
    return out


def render(facts: list[Fact], scope: str, private: bool) -> str:
    lines = [f"# {title(scope, private)}", "", INTRO]
    by_category: dict[str, list[Fact]] = {}
    for fact in facts:
        by_category.setdefault(fact.category, []).append(fact)
    for category in sorted(by_category, key=lambda c: (c != "general", c)):
        lines += ["", f"## {category}", ""]
        for fact in by_category[category]:
            lines.append(f"- {fact.content} <!-- {_format_meta(fact)} -->")
    return "\n".join(lines) + "\n"


def _parse_meta(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for token in text.split():
        key, sep, value = token.partition("=")
        if sep:
            meta[key] = value
    return meta


def _format_meta(fact: Fact) -> str:
    parts = [f"id={fact.id}", f"by={_token(fact.by) or USER}"]
    if fact.model:
        parts.append(f"model={_token(fact.model)}")
    if fact.at:
        parts.append(f"at={_token(fact.at)}")
    return " ".join(parts)


def _token(value: str) -> str:
    return "".join(value.split()).replace("-->", "")
