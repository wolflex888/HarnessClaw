from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from harness_claw.session import Session


class SessionStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._sessions: dict[str, Session] = {}
        if path.exists():
            data = json.loads(path.read_text())
            for item in data:
                s = Session.from_dict(item)
                self._sessions[s.session_id] = s

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def all(self) -> list[Session]:
        return list(self._sessions.values())

    def save(self, session: Session) -> None:
        self._sessions[session.session_id] = session
        self._flush()

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._flush()

    def grouped_by_dir(self) -> dict[str, list[Session]]:
        result: dict[str, list[Session]] = defaultdict(list)
        for s in self._sessions.values():
            result[s.working_dir].append(s)
        return dict(result)

    def _flush(self) -> None:
        self._path.write_text(
            json.dumps([s.to_dict() for s in self._sessions.values()], indent=2)
        )
