"""The tidy-up: merge facts that say the same thing, drop what isn't a lasting fact.

A model proposes changes; this module decides which to apply:
- a merged line may only use words that were already in the lines it replaces,
  so the model can't invent or embellish, and must keep most of each line's words;
- merged lines must share words, so facts about different things stay apart;
- each drop is double-checked with a single yes/no question, which small local
  models answer far more reliably than they edit a whole list;
- facts you wrote yourself are never merged or dropped;
- one pass removes at most a fifth of a file (at least four lines);
- every applied change is logged and can be undone.

Each memory file is tidied on its own, so personal, project and private facts
never get mixed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from harnessclaw.llm import ChatModel
from harnessclaw.memory.facts import Fact, clean_category, now
from harnessclaw.memory.search import STOPWORDS, tokens
from harnessclaw.memory.store import MemoryStore

MIN_FACTS = 12  # files smaller than this are left alone
MAX_FACTS = 150  # newest first; bigger files are tidied over several passes
MAX_REMOVED_SHARE = 0.2
MIN_REMOVED = 4
MAX_MERGED_LENGTH = 300
MIN_KEPT_SHARE = 0.5  # of each merged line's words that must survive the merge

PROMPT = """You maintain a personal assistant's long-term memory: short facts about one person, collected over time. Propose edits that make the list shorter and more accurate without losing anything worth keeping.

Allowed edits:
- merge: two or more lines that say the same thing, or where one adds detail to another, become one line that keeps every detail (names, numbers, dates). If a fact changed over time (a move, a new job), keep the newer one and mention the change when it matters, for example "Lives in Taipei (moved from Tokyo in 2026)".
- drop: a line that isn't a lasting fact about the person, such as a one-off request, a finished errand, or today's weather.

Use only words that already appear in the lines you merge. Don't merge lines about different people or topics. Leave lines that are fine alone. Keep the person's wording and language. If nothing needs changing, answer [].

Lines ({count}):
{lines}

Answer with only a JSON array, no other text. Each item is one of:
{{"op": "merge", "ids": ["m_...", "m_..."], "content": "the single merged line"}}
{{"op": "drop", "id": "m_...", "reason": "<why, in a few words>"}}"""

DROP_CHECK = """One line from a personal assistant's memory about a person:

"{content}"

Would this still be useful to know about them a month from now? Answer only yes or no."""

_WORD_RE = re.compile(r"[^\W_]+")
_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


@dataclass
class Proposal:
    op: str  # merge | drop
    ids: list[str]
    content: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, "ids": self.ids, "content": self.content, "reason": self.reason}


@dataclass
class TidyReport:
    applied: list[Proposal] = field(default_factory=list)
    rejected: list[tuple[Proposal, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    change_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": [p.to_dict() for p in self.applied],
            "rejected": [{**p.to_dict(), "why": why} for p, why in self.rejected],
            "errors": self.errors,
            "changes": self.change_ids,
        }


def parse_reply(reply: str) -> list[dict[str, Any]]:
    start, end = reply.find("["), reply.rfind("]")
    if start == -1 or end < start:
        raise ValueError("no JSON array in the model's answer")
    data = json.loads(reply[start : end + 1])
    if not isinstance(data, list):
        raise ValueError("the model's answer is not a list")
    return [item for item in data if isinstance(item, dict)]


def check(items: list[dict[str, Any]], facts: list[Fact]) -> tuple[list[Proposal], list[tuple[Proposal, str]]]:
    """Split the model's proposals into those safe to apply and those rejected (with why)."""
    by_id = {f.id: f for f in facts}
    budget = max(MIN_REMOVED, int(len(facts) * MAX_REMOVED_SHARE))
    used: set[str] = set()
    accepted: list[Proposal] = []
    rejected: list[tuple[Proposal, str]] = []

    for item in items:
        op = item.get("op")
        if op == "merge":
            ids = [str(i) for i in item.get("ids") or [] if isinstance(i, str)]
            prop = Proposal("merge", ids, content=" ".join(str(item.get("content", "")).split()))
        elif op == "drop":
            prop = Proposal("drop", [str(item.get("id", ""))], reason=str(item.get("reason", ""))[:200])
        else:
            continue

        why = _problem(prop, by_id, used)
        removes = len(prop.ids) - 1 if prop.op == "merge" else 1
        if why is None and removes > budget:
            why = "over this pass's limit; left for the next pass"
        if why is not None:
            rejected.append((prop, why))
            continue
        budget -= removes
        used.update(prop.ids)
        accepted.append(prop)
    return accepted, rejected


def _problem(prop: Proposal, by_id: dict[str, Fact], used: set[str]) -> str | None:
    if prop.op == "merge" and len(set(prop.ids)) < 2:
        return "a merge needs at least two lines"
    if len(set(prop.ids)) != len(prop.ids):
        return "the same line appears twice"
    for fid in prop.ids:
        if fid not in by_id:
            return f"no line {fid}"
        if fid in used:
            return f"{fid} is already part of another change"
        if by_id[fid].user_written:
            return f"{fid} was written by you"
    if prop.op == "merge":
        if not prop.content:
            return "the merged line is empty"
        if len(prop.content) > MAX_MERGED_LENGTH:
            return "the merged line is too long"
        sources = [by_id[i].content for i in prop.ids]
        new_words = _new_words(prop.content, sources)
        if new_words:
            return "the merged line adds words: " + ", ".join(sorted(new_words)[:5])
        if not _related(sources):
            return "the lines are about different things"
        kept = tokens(prop.content)
        for src in sources:
            words = tokens(src)
            if words and len(words & kept) / len(words) < MIN_KEPT_SHARE:
                return f"the merged line loses most of \"{src}\""
    return None


def _related(sources: list[str]) -> bool:
    """Every line shares at least one real word with the others."""
    sets = [tokens(s) for s in sources]
    for i, words in enumerate(sets):
        others = set().union(*(w for j, w in enumerate(sets) if j != i))
        if not words & others:
            return False
    return True


async def _worth_keeping(model: ChatModel, fact: Fact) -> bool:
    """The model's answer to one focused question. Anything but a clear "no" keeps it."""
    try:
        answer = await model.complete(DROP_CHECK.format(content=fact.content))
    except Exception:  # noqa: BLE001 - when in doubt, keep
        return True
    return not answer.strip().lower().lstrip("*\"' ").startswith("no")


def _new_words(merged: str, sources: list[str]) -> set[str]:
    source_words = {w.lower() for s in sources for w in _WORD_RE.findall(s)}
    source_chars = {c for s in sources for c in _CJK_RE.findall(s)}
    new: set[str] = set()
    for word in _WORD_RE.findall(merged):
        w = word.lower()
        if _CJK_RE.search(w):
            new.update(c for c in _CJK_RE.findall(w) if c not in source_chars)
            continue
        if w in source_words or w in STOPWORDS:
            continue
        if w.isdigit():
            new.add(w)
            continue
        # Allow another form of a word that was there ("moved" for "moves").
        if len(w) >= 4 and any(len(s) >= 4 and s[:4] == w[:4] for s in source_words):
            continue
        new.add(w)
    return new


def _newest(facts: list[Fact]) -> Fact:
    return max(facts, key=lambda f: f.at)


async def tidy_file(
    store: MemoryStore, facts: list[Fact], model: ChatModel, *, dry_run: bool = False
) -> TidyReport:
    report = TidyReport()
    if len(facts) < MIN_FACTS:
        return report
    facts = sorted(facts, key=lambda f: f.at, reverse=True)[:MAX_FACTS]
    lines = "\n".join(f"{f.id} ({f.category}) {f.content}" for f in facts)
    try:
        reply = await model.complete(PROMPT.format(count=len(facts), lines=lines))
        items = parse_reply(reply)
    except Exception as exc:  # noqa: BLE001 - a failed tidy-up must never touch memory
        report.errors.append(f"{type(exc).__name__}: {exc}")
        return report

    accepted, report.rejected = check(items, facts)
    by_id = {f.id: f for f in facts}
    for prop in list(accepted):
        if prop.op == "drop" and await _worth_keeping(model, by_id[prop.ids[0]]):
            accepted.remove(prop)
            report.rejected.append((prop, "asked on its own, the model said it's worth keeping"))
    for prop in accepted:
        if dry_run:
            report.applied.append(prop)
            continue
        sources = [by_id[i] for i in prop.ids]
        if prop.op == "merge":
            keep = _newest(sources)
            merged = Fact(
                id=keep.id, content=prop.content, category=clean_category(keep.category),
                scope=keep.scope, private=keep.private, by="tidy", model=model.name, at=now(),
            )
            change = store.replace("merge", "tidy", prop.ids, [merged], reason="same fact")
        else:
            change = store.replace("drop", "tidy", prop.ids, [], reason=prop.reason)
        if change is None:
            report.rejected.append((prop, "the lines changed while tidying"))
            continue
        report.applied.append(prop)
        report.change_ids.append(change.id)
    return report


async def tidy(store: MemoryStore, model: ChatModel, *, dry_run: bool = False) -> TidyReport:
    """One pass over every memory file."""
    total = TidyReport()
    for facts in store.file_facts().values():
        report = await tidy_file(store, facts, model, dry_run=dry_run)
        total.applied += report.applied
        total.rejected += report.rejected
        total.errors += report.errors
        total.change_ids += report.change_ids
    return total
