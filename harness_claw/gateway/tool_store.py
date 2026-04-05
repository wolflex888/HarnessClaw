from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ToolDef:
    id: str                     # unique key, e.g. "run_tests"
    name: str                   # display name
    description: str            # LLM-readable description
    input_schema: dict          # JSON Schema for call parameters
    output_schema: dict         # JSON Schema for structured return value
    adapter: str                # "cli" | "http" | "mcp"
    adapter_config: dict        # backend-specific config
    cap_allowlist: list[str]    # caps required ([] = no agent sees this tool)
    allowed_roles: list[str]    # role IDs allowed ([] = all roles pass)
    required_scopes: list[str]  # scopes required ([] = no scope gate)
    metadata: dict              # scheduler hints, tags, etc.
    created_at: str             # ISO 8601
    updated_at: str             # ISO 8601

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "adapter": self.adapter,
            "adapter_config": self.adapter_config,
            "cap_allowlist": self.cap_allowlist,
            "allowed_roles": self.allowed_roles,
            "required_scopes": self.required_scopes,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tools (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL,
    input_schema    TEXT NOT NULL,
    output_schema   TEXT NOT NULL,
    adapter         TEXT NOT NULL,
    adapter_config  TEXT NOT NULL,
    cap_allowlist   TEXT NOT NULL,
    allowed_roles   TEXT NOT NULL,
    required_scopes TEXT NOT NULL,
    metadata        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
)
"""


def _row_to_tool(row: sqlite3.Row) -> ToolDef:
    return ToolDef(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        input_schema=json.loads(row["input_schema"]),
        output_schema=json.loads(row["output_schema"]),
        adapter=row["adapter"],
        adapter_config=json.loads(row["adapter_config"]),
        cap_allowlist=json.loads(row["cap_allowlist"]),
        allowed_roles=json.loads(row["allowed_roles"]),
        required_scopes=json.loads(row["required_scopes"]),
        metadata=json.loads(row["metadata"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SqliteToolStore:
    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, tool: ToolDef) -> None:
        now = datetime.now(timezone.utc).isoformat()
        tool.updated_at = now
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tools (id, name, description, input_schema, output_schema,
                    adapter, adapter_config, cap_allowlist, allowed_roles, required_scopes,
                    metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    input_schema=excluded.input_schema,
                    output_schema=excluded.output_schema,
                    adapter=excluded.adapter,
                    adapter_config=excluded.adapter_config,
                    cap_allowlist=excluded.cap_allowlist,
                    allowed_roles=excluded.allowed_roles,
                    required_scopes=excluded.required_scopes,
                    metadata=excluded.metadata,
                    updated_at=excluded.updated_at
                """,
                (
                    tool.id, tool.name, tool.description,
                    json.dumps(tool.input_schema),
                    json.dumps(tool.output_schema),
                    tool.adapter,
                    json.dumps(tool.adapter_config),
                    json.dumps(tool.cap_allowlist),
                    json.dumps(tool.allowed_roles),
                    json.dumps(tool.required_scopes),
                    json.dumps(tool.metadata),
                    tool.created_at,
                    now,
                ),
            )

    def get(self, tool_id: str) -> ToolDef | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tools WHERE id = ?", (tool_id,)
            ).fetchone()
        return _row_to_tool(row) if row else None

    def list(self) -> list[ToolDef]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tools ORDER BY created_at"
            ).fetchall()
        return [_row_to_tool(r) for r in rows]

    def delete(self, tool_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM tools WHERE id = ?", (tool_id,)
            )
        return cursor.rowcount > 0
