"""What the server exposes: recall, remember, forget, tidy — over one store."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from harnessclaw.llm import ChatModel
from harnessclaw.memory import tidy as tidy_mod
from harnessclaw.memory.embeddings import SemanticIndex
from harnessclaw.memory.facts import PERSONAL, PROJECT_PREFIX, Fact, project_scope
from harnessclaw.memory.search import select
from harnessclaw.memory.store import MemoryStore

log = logging.getLogger(__name__)

TIDY_EVERY_NEW = 8  # facts added since the last tidy-up…
TIDY_EVERY = timedelta(days=7)  # …or this long, whichever comes first
CONTEXT_MAX_CHARS = 9000


class MemoryService:
    def __init__(
        self,
        store: MemoryStore,
        semantic: SemanticIndex | None = None,
        tidy_model: ChatModel | None = None,
        recall_limit: int = 20,
        state_path: Path | None = None,
    ) -> None:
        self.store = store
        self.semantic = semantic
        self.tidy_model = tidy_model
        self.recall_limit = recall_limit
        self._state_path = state_path or store.root.parent / "state.json"
        self._tidying = False

    # ------------------------------------------------------------------ recall

    def visible(self, project: str | None, include_private: bool) -> list[Fact]:
        scopes = {PERSONAL, project_scope(project)}
        return self.store.facts(scopes, include_private=include_private)

    async def recall(
        self,
        query: str,
        *,
        project: str | None = None,
        limit: int | None = None,
        include_private: bool = False,
        pad_with_recent: bool = True,
    ) -> list[Fact]:
        facts = self.visible(project, include_private)
        limit = self.recall_limit if limit is None else limit
        query_vec = vectors = None
        if self.semantic is not None and query.strip():
            if await self.semantic.ensure(facts):
                query_vec = await self.semantic.query_vector(query)
                vectors = self.semantic.vectors_for(facts)
        return select(
            query, facts, limit, query_vec=query_vec, vectors=vectors, pad_with_recent=pad_with_recent
        )

    # ------------------------------------------------------------------ writing

    async def remember(
        self,
        content: str,
        *,
        category: str | None = None,
        project: str | None = None,
        private: bool = False,
        by: str,
        model: str = "",
        replaces: str | None = None,
        include_private: bool = False,
    ) -> tuple[Fact, bool]:
        """Returns (fact, changed). `changed` is False when it was already remembered."""
        scope = project_scope(project)
        if replaces:
            old = self.store.get(replaces)
            if old is None or (old.private and not include_private):
                raise KeyError(f"no memory {replaces}")
            # An update stays where the old fact was unless told otherwise, and never
            # makes a private fact visible to everyone.
            if project is None:
                scope = old.scope
            private = private or old.private
        fact, change = self.store.remember(
            content, category=category, scope=scope, private=private,
            by=by, model=model, replaces=replaces,
        )
        if change is not None and self.semantic is not None:
            await self.semantic.ensure([fact])
        return fact, change is not None

    def forget(self, fact_id: str, *, by: str, include_private: bool = False) -> Fact | None:
        fact = self.store.get(fact_id)
        if fact is None or (fact.private and not include_private):
            return None
        self.store.forget(fact_id, by=by)
        return fact

    # ------------------------------------------------------------------ upkeep

    async def refresh_index(self) -> bool:
        if self.semantic is None:
            return False
        facts = self.store.facts()
        self.semantic.prune({f.id for f in facts})
        return await self.semantic.ensure(facts)

    def tidy_due(self) -> bool:
        if self.tidy_model is None or self._tidying:
            return False
        count = len(self.store.facts())
        if count < tidy_mod.MIN_FACTS:
            return False
        state = self._state()
        last = state.get("tidied_at")
        if not last:
            return True
        if count - int(state.get("count", 0)) >= TIDY_EVERY_NEW:
            return True
        return datetime.now(UTC) - datetime.fromisoformat(last) >= TIDY_EVERY

    async def tidy(self, *, dry_run: bool = False) -> tidy_mod.TidyReport:
        if self.tidy_model is None:
            raise RuntimeError("no tidy model configured; set [tidy] in config.toml")
        if self._tidying:
            raise RuntimeError("a tidy-up is already running")
        self._tidying = True
        try:
            report = await tidy_mod.tidy(self.store, self.tidy_model, dry_run=dry_run)
        finally:
            self._tidying = False
        if not dry_run and not report.errors:
            self._save_state(
                {"tidied_at": datetime.now(UTC).isoformat(), "count": len(self.store.facts())}
            )
        return report

    def status(self) -> dict[str, object]:
        facts = self.store.facts()
        return {
            "facts": len(facts),
            "private": sum(f.private for f in facts),
            "projects": sorted({f.scope[len(PROJECT_PREFIX):] for f in facts if f.scope != PERSONAL}),
            "embeddings": self.semantic.status() if self.semantic else None,
            "tidy_model": self.tidy_model.name if self.tidy_model else None,
            "last_tidy": self._state().get("tidied_at"),
        }

    def _state(self) -> dict[str, object]:
        try:
            return json.loads(self._state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: dict[str, object]) -> None:
        self._state_path.write_text(json.dumps(state))


def render_context(facts: list[Fact], project: str | None = None) -> str:
    """The memory block a harness adds to the model's prompt."""
    if not facts:
        return ""
    scope = project_scope(project)
    where = f' and the current project "{scope[len(PROJECT_PREFIX):]}"' if scope != PERSONAL else ""
    lines = [f"Memory (HarnessClaw): facts about the user{where} saved in earlier sessions, most relevant first."]
    for f in facts:
        tag = f"{f.id}, {f.category}" + (", project" if f.scope != PERSONAL else "")
        lines.append(f"- {f.content} [{tag}]")
    lines.append(
        "New lasting facts can be saved with the memory `remember` tool; when one of these "
        "facts has changed, pass its id as `replaces`."
    )
    text = "\n".join(lines)
    if len(text) > CONTEXT_MAX_CHARS:
        text = text[: CONTEXT_MAX_CHARS - 1].rsplit("\n", 1)[0]
    return text
