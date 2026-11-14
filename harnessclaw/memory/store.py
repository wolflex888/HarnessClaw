"""The memory store: Markdown files on disk, plus a change log for undo.

The files are the source of truth. Before every operation the store re-reads
any file that changed on disk, so hand edits are picked up (and logged) rather
than overwritten. Every change, including hand edits, goes into
`history.jsonl` with the facts it replaced, so any of them can be undone.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harnessclaw.memory import markdown
from harnessclaw.memory.facts import (
    PERSONAL,
    USER,
    Fact,
    clean_category,
    clean_content,
    new_id,
    now,
)


@dataclass
class Change:
    id: str
    at: str
    action: str  # remember | update | forget | edit | merge | drop | undo
    by: str
    before: list[Fact] = field(default_factory=list)
    after: list[Fact] = field(default_factory=list)
    reason: str = ""
    undoes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "at": self.at,
            "action": self.action,
            "by": self.by,
            "before": [f.to_dict() for f in self.before],
            "after": [f.to_dict() for f in self.after],
            "reason": self.reason,
            "undoes": self.undoes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Change:
        return cls(
            id=data["id"],
            at=data["at"],
            action=data["action"],
            by=data.get("by", USER),
            before=[Fact.from_dict(f) for f in data.get("before", [])],
            after=[Fact.from_dict(f) for f in data.get("after", [])],
            reason=data.get("reason", ""),
            undoes=data.get("undoes", ""),
        )


FileKey = tuple[str, bool]  # (scope, private)


@dataclass
class _FileState:
    stamp: tuple[int, int] | None  # (mtime_ns, size) of the file as we last saw it
    facts: dict[str, Fact]  # in file order


class MemoryStore:
    def __init__(self, home: Path) -> None:
        self.root = home / "memory"
        self.history_path = home / "history.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "projects").mkdir(exist_ok=True)
        self._lock = threading.RLock()
        self._files: dict[FileKey, _FileState] = {}
        self._loaded = False
        self.sync()

    # ------------------------------------------------------------------ reading

    def sync(self) -> list[Change]:
        """Re-read files that changed on disk. Returns the hand edits found."""
        with self._lock:
            changes: list[Change] = []
            on_disk: dict[FileKey, Path] = {}
            for path in [*self.root.glob("*.md"), *(self.root / "projects").glob("*.md")]:
                try:
                    on_disk[markdown.path_scope(self.root, path)] = path
                except ValueError:
                    continue
            for key in set(self._files) | set(on_disk):
                change = self._sync_file(key, on_disk.get(key))
                if change is not None:
                    changes.append(change)
            self._loaded = True
            return changes

    def _sync_file(self, key: FileKey, path: Path | None) -> Change | None:
        state = self._files.get(key)
        if path is None:
            if state is None or state.stamp is None:
                return None
            new_facts: list[Fact] = []
            stamp = None
            needs_ids = False
        else:
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            if state is not None and state.stamp == stamp:
                return None
            parsed = markdown.parse(path.read_text(encoding="utf-8"), *key)
            new_facts, needs_ids = parsed.facts, parsed.needs_ids

        old = state.facts if state is not None else {}
        self._files[key] = _FileState(stamp=stamp, facts={f.id: f for f in new_facts})
        if needs_ids:
            self._write(key)

        if not self._loaded:
            return None  # first read at startup: nothing to compare against
        new = self._files[key].facts
        before = [f for fid, f in old.items() if fid not in new or _differs(f, new[fid])]
        after = [f for fid, f in new.items() if fid not in old or _differs(old[fid], f)]
        if not before and not after:
            return None
        change = Change(id=_change_id(), at=now(), action="edit", by=USER, before=before, after=after)
        self._log(change)
        return change

    def facts(self, scopes: Iterable[str] | None = None, include_private: bool = True) -> list[Fact]:
        with self._lock:
            self.sync()
            wanted = set(scopes) if scopes is not None else None
            out: list[Fact] = []
            for (scope, private), state in self._files.items():
                if wanted is not None and scope not in wanted:
                    continue
                if private and not include_private:
                    continue
                out.extend(state.facts.values())
            return out

    def get(self, fact_id: str) -> Fact | None:
        with self._lock:
            self.sync()
            return self._find(fact_id)

    def file_facts(self) -> dict[FileKey, list[Fact]]:
        with self._lock:
            self.sync()
            return {k: list(s.facts.values()) for k, s in self._files.items() if s.facts}

    # ------------------------------------------------------------------ writing

    def remember(
        self,
        content: str,
        *,
        category: str | None = None,
        scope: str = PERSONAL,
        private: bool = False,
        by: str = USER,
        model: str = "",
        replaces: str | None = None,
    ) -> tuple[Fact, Change | None]:
        """Add a fact, or update the one `replaces` names. Returns (fact, change);
        change is None when an identical fact already existed."""
        content = clean_content(content)
        if not content:
            raise ValueError("a memory needs some content")
        with self._lock:
            self.sync()
            old = self._find(replaces) if replaces else None
            if old is None:
                existing = self._duplicate(content, scope, private)
                if existing is not None:
                    return existing, None
                fact = Fact(
                    id=new_id(), content=content, category=clean_category(category), scope=scope,
                    private=private, by=by, model=model, at=now(),
                )
                return fact, self._commit("remember", by, [], [fact])
            fact = Fact(
                id=old.id,
                content=content,
                category=clean_category(category) if category else old.category,
                scope=scope,
                private=private,
                by=by,
                model=model,
                at=now(),
            )
            if not _differs(old, fact) and (old.scope, old.private) == (scope, private):
                return old, None
            return fact, self._commit("update", by, [old], [fact])

    def forget(self, fact_id: str, *, by: str = USER, reason: str = "") -> Change | None:
        with self._lock:
            self.sync()
            fact = self._find(fact_id)
            if fact is None:
                return None
            return self._commit("forget", by, [fact], [], reason=reason)

    def replace(
        self, action: str, by: str, before_ids: list[str], after: list[Fact], reason: str = ""
    ) -> Change | None:
        """Swap facts for others in one logged change (used by the tidy-up)."""
        with self._lock:
            self.sync()
            before = [f for f in (self._find(i) for i in before_ids) if f is not None]
            if len(before) != len(before_ids):
                return None  # something changed under us; skip this one
            return self._commit(action, by, before, after, reason=reason)

    def undo(self, change_id: str, *, by: str = USER) -> Change | None:
        with self._lock:
            self.sync()
            target = next((c for c in self._read_history() if c.id == change_id), None)
            if target is None:
                raise KeyError(change_id)
            if change_id in self._undone_ids():
                raise ValueError("that change was already undone")
            # Take out what the change put in (as it is now), put back what it took out.
            current = [f for f in (self._find(f.id) for f in target.after) if f is not None]
            current_ids = {f.id for f in current}
            restore = [f for f in target.before if self._find(f.id) is None or f.id in current_ids]
            if not current and not restore:
                return None
            return self._commit("undo", by, current, restore, undoes=change_id)

    # ------------------------------------------------------------------ history

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            undone = self._undone_ids()
            changes = self._read_history()[-limit:][::-1]
            return [{**c.to_dict(), "undone": c.id in undone} for c in changes]

    def _read_history(self) -> list[Change]:
        if not self.history_path.exists():
            return []
        out = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(Change.from_dict(json.loads(line)))
                except (ValueError, KeyError):
                    continue
        return out

    def _undone_ids(self) -> set[str]:
        return {c.undoes for c in self._read_history() if c.undoes}

    def _log(self, change: Change) -> None:
        with self.history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(change.to_dict(), ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ internals

    def _find(self, fact_id: str | None) -> Fact | None:
        if not fact_id:
            return None
        for state in self._files.values():
            if fact_id in state.facts:
                return state.facts[fact_id]
        return None

    def _duplicate(self, content: str, scope: str, private: bool) -> Fact | None:
        state = self._files.get((scope, private))
        if state is None:
            return None
        norm = content.casefold()
        return next((f for f in state.facts.values() if f.content.casefold() == norm), None)

    def _commit(
        self, action: str, by: str, before: list[Fact], after: list[Fact],
        reason: str = "", undoes: str = "",
    ) -> Change:
        touched: set[FileKey] = set()
        after_ids = {f.id for f in after}
        for fact in before:
            key = (fact.scope, fact.private)
            state = self._files.get(key)
            if state is None:
                continue
            if fact.id in after_ids and any(
                (a.scope, a.private) == key for a in after if a.id == fact.id
            ):
                continue  # replaced in place below, keeping its position
            state.facts.pop(fact.id, None)
            touched.add(key)
        for fact in after:
            key = (fact.scope, fact.private)
            state = self._files.setdefault(key, _FileState(stamp=None, facts={}))
            state.facts[fact.id] = fact
            touched.add(key)
        for key in touched:
            self._write(key)
        change = Change(
            id=_change_id(), at=now(), action=action, by=by, before=before, after=after,
            reason=reason, undoes=undoes,
        )
        self._log(change)
        return change

    def _write(self, key: FileKey) -> None:
        state = self._files[key]
        path = markdown.scope_path(self.root, *key)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = markdown.render(list(state.facts.values()), *key)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        stat = path.stat()
        state.stamp = (stat.st_mtime_ns, stat.st_size)


def _differs(a: Fact, b: Fact) -> bool:
    return a.content != b.content or a.category != b.category


def _change_id() -> str:
    return "c_" + new_id()[2:]
