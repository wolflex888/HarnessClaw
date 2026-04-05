from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PlanTask:
    index: int       # 1-based task number
    title: str       # e.g. "Add RoleConfig fields"
    full_text: str   # complete markdown block for this task


_TASK_PATTERN = re.compile(r"^## Task \d+:\s*(.+)$", re.MULTILINE)
_TASK_SPLIT = re.compile(r"^## Task \d+:", re.MULTILINE)


def parse_plan(path: str | Path) -> list[PlanTask]:
    """Extract tasks from a plan markdown file produced by superpowers:writing-plans."""
    text = Path(path).read_text()   # raises FileNotFoundError if missing

    # Find all task headers with their titles
    matches = list(_TASK_PATTERN.finditer(text))

    # Split text by task headers to get bodies
    parts = _TASK_SPLIT.split(text)
    # parts[0] is the preamble before the first ## Task header; parts[1:] are task bodies

    tasks = []
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        # Reconstruct the header from the original text
        header = text[match.start():match.end()]
        # Get the body from the corresponding part (accounting for preamble in parts[0])
        body = parts[i + 1] if i + 1 < len(parts) else ""
        full_text = (header + "\n" + body).strip()
        tasks.append(PlanTask(index=i + 1, title=title, full_text=full_text))

    return tasks
