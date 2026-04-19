from __future__ import annotations

import json
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from harness_claw.gateway.redaction import Redactor


@dataclass
class BackfillReport:
    audit_records_scanned: int = 0
    audit_records_changed: int = 0
    memory_entries_scanned: int = 0
    memory_entries_changed: int = 0
    audit_by_category: dict[str, int] = field(default_factory=dict)
    memory_by_category: dict[str, int] = field(default_factory=dict)


def _count_categories(before: str, after: str, into: dict[str, int]) -> None:
    # Rough category accounting: count every [REDACTED:<name>] token that
    # appears in `after` but not in `before`.
    import re
    tag = re.compile(r"\[REDACTED:([a-z_]+)\]")
    for m in tag.finditer(after):
        name = m.group(1)
        # Only count tokens that weren't already present.
        before_count = before.count(f"[REDACTED:{name}]")
        after_count = after.count(f"[REDACTED:{name}]")
        delta = after_count - before_count
        if delta > 0 and into.get(name, 0) < after_count:
            into[name] = into.get(name, 0) + 1


async def _backfill_audit(
    redactor: Redactor, path: Path, apply: bool, report: BackfillReport,
) -> None:
    if not path.exists():
        return

    tmp = path.with_suffix(path.suffix + ".tmp")
    with path.open("r") as src, tmp.open("w") as dst:
        for line in src:
            if not line.strip():
                dst.write(line)
                continue
            report.audit_records_scanned += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                dst.write(line)
                continue

            original_details = record.get("details") or {}
            new_details = redactor.redact(original_details)
            if new_details != original_details:
                report.audit_records_changed += 1
                _count_categories(
                    json.dumps(original_details), json.dumps(new_details),
                    report.audit_by_category,
                )
                record["details"] = new_details
            dst.write(json.dumps(record) + "\n")

    if apply and report.audit_records_changed > 0:
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
        os.replace(tmp, path)
    else:
        tmp.unlink(missing_ok=True)


def _check_memory_lock(path: Path) -> None:
    """Raise RuntimeError if another process holds a write lock on memory.db."""
    if not path.exists():
        return
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
    except sqlite3.OperationalError as e:
        raise RuntimeError(
            f"memory.db is locked by another process — stop the server before "
            f"running redact-existing: {e}"
        )
    finally:
        conn.close()


async def _backfill_memory(
    redactor: Redactor, path: Path, apply: bool, report: BackfillReport,
) -> None:
    if not path.exists():
        return

    _check_memory_lock(path)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT namespace, key, value, summary FROM memory"
        ).fetchall()
        report.memory_entries_scanned = len(rows)

        updates: list[tuple[str, str | None, str, str]] = []
        for row in rows:
            new_value = redactor.redact(row["value"])
            new_summary = (
                redactor.redact(row["summary"]) if row["summary"] is not None else None
            )
            if new_value != row["value"] or new_summary != row["summary"]:
                report.memory_entries_changed += 1
                _count_categories(
                    f"{row['value']} {row['summary'] or ''}",
                    f"{new_value} {new_summary or ''}",
                    report.memory_by_category,
                )
                updates.append((new_value, new_summary, row["namespace"], row["key"]))

        if apply and updates:
            conn.executemany(
                "UPDATE memory SET value=?, summary=? WHERE namespace=? AND key=?",
                updates,
            )
            conn.commit()
    finally:
        conn.close()


async def run_backfill(
    *,
    redactor: Redactor,
    audit_path: Path,
    memory_path: Path,
    apply: bool,
) -> BackfillReport:
    report = BackfillReport()
    await _backfill_audit(redactor, audit_path, apply, report)
    await _backfill_memory(redactor, memory_path, apply, report)
    return report
