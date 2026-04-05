from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from harness_claw.gateway.event_bus import Event, EventBus
from harness_claw.gateway.task_store import Task
from harness_claw.runtime.plan_parser import PlanTask, parse_plan

MAX_REWRITE_ROUNDS = 5


class PlanExecutor:
    """Drives the per-task implementation loop for a plan file.

    For each task: delegates to code-writer, fans out to spec-reviewer and
    code-reviewer in parallel, and loops until both approve or MAX_REWRITE_ROUNDS
    is exceeded.
    """

    def __init__(
        self,
        broker: Any,
        event_bus: EventBus,
        on_progress: Callable[[str], Awaitable[None]],
    ) -> None:
        self._broker = broker
        self._event_bus = event_bus
        self._on_progress = on_progress

    async def execute(self, plan_path: str) -> str:
        tasks = parse_plan(plan_path)
        total = len(tasks)
        lines: list[str] = []
        for task in tasks:
            line = await self._run_task(task, total)
            lines.append(line)
            if line.startswith(("STUCK:", "BLOCKED:")):
                break
        return "\n".join(lines)

    async def _run_task(self, task: PlanTask, total: int) -> str:
        prefix = f"[Task {task.index}/{total}]"

        await self._on_progress(f"{prefix} Implementing: {task.title}...")
        writer_tid = await self._broker.delegate(
            delegated_by="plan-executor",
            caps=["python", "typescript"],
            instructions=task.full_text,
        )
        writer_task = await self._wait_for_task(writer_tid)

        if writer_task.status == "failed" or (
            isinstance(writer_task.result, dict)
            and str(writer_task.result.get("status", "")).upper() == "BLOCKED"
        ):
            await self._on_progress(f"{prefix} BLOCKED — stopping")
            return f"BLOCKED: {task.title}"

        writer_summary = self._extract_summary(writer_task.result)

        prev_spec_findings: list | None = None
        prev_quality_findings: list | None = None

        for round_num in range(1, MAX_REWRITE_ROUNDS + 1):
            await self._on_progress(f"{prefix} Reviewing in parallel (round {round_num})...")

            spec_instr = self._spec_review_prompt(task, writer_summary, prev_spec_findings, round_num)
            quality_instr = self._quality_review_prompt(task, writer_summary, prev_quality_findings, round_num)

            spec_tid, quality_tid = await asyncio.gather(
                self._broker.delegate(
                    delegated_by="plan-executor",
                    caps=["spec-review"],
                    instructions=spec_instr,
                ),
                self._broker.delegate(
                    delegated_by="plan-executor",
                    caps=["code-review"],
                    instructions=quality_instr,
                ),
            )

            spec_task, quality_task = await asyncio.gather(
                self._wait_for_task(spec_tid),
                self._wait_for_task(quality_tid),
            )

            spec_v = self._parse_verdict(spec_task)
            quality_v = self._parse_verdict(quality_task)

            if spec_v["verdict"] == "APPROVE" and quality_v["verdict"] == "APPROVE":
                await self._on_progress(f"{prefix} APPROVED — moving to next task")
                return f"DONE: {task.title}"

            findings_parts: list[str] = []
            if spec_v["verdict"] == "REVISE":
                prev_spec_findings = spec_v.get("findings", [])
                findings_parts.append(
                    f"Spec review findings:\n{json.dumps(prev_spec_findings, indent=2)}"
                )
            else:
                prev_spec_findings = None

            if quality_v["verdict"] == "REVISE":
                prev_quality_findings = quality_v.get("findings", [])
                findings_parts.append(
                    f"Code quality findings:\n{json.dumps(prev_quality_findings, indent=2)}"
                )
            else:
                prev_quality_findings = None

            if round_num == MAX_REWRITE_ROUNDS:
                await self._on_progress(f"{prefix} STUCK after {MAX_REWRITE_ROUNDS} rounds — stopping")
                return f"STUCK: {task.title}\n" + "\n\n".join(findings_parts)

            await self._on_progress(
                f"{prefix} REVISE — sending findings to code-writer (round {round_num + 1})"
            )
            rewrite_instr = self._rewrite_prompt(task, "\n\n".join(findings_parts))
            rewrite_tid = await self._broker.delegate(
                delegated_by="plan-executor",
                caps=["python", "typescript"],
                instructions=rewrite_instr,
            )
            rewrite_task = await self._wait_for_task(rewrite_tid)
            writer_summary = self._extract_summary(rewrite_task.result)

        return f"STUCK: {task.title}"  # unreachable but satisfies type checker

    async def _wait_for_task(self, task_id: str) -> Task:
        done = asyncio.Event()

        async def on_event(_: Event) -> None:
            done.set()

        sub_ok = await self._event_bus.subscribe(f"task:{task_id}:completed", on_event)
        sub_fail = await self._event_bus.subscribe(f"task:{task_id}:failed", on_event)

        # Guard against task that already completed before we subscribed
        existing = self._broker.get_task(task_id)
        if existing and existing.status in ("completed", "failed"):
            done.set()

        await done.wait()
        await self._event_bus.unsubscribe(sub_ok)
        await self._event_bus.unsubscribe(sub_fail)
        task = self._broker.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Task {task_id!r} disappeared from broker after completion event")
        return task

    def _extract_summary(self, result: Any) -> str:
        if isinstance(result, dict):
            return result.get("summary", json.dumps(result))
        return str(result or "")

    def _parse_verdict(self, task: Task) -> dict:
        result = task.result
        if isinstance(result, dict) and "verdict" in result:
            return result
        try:
            parsed = json.loads(str(result))
            if isinstance(parsed, dict) and "verdict" in parsed:
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        text = str(result or "")
        return {
            "verdict": "APPROVE" if "APPROVE" in text else "REVISE",
            "findings": [],
            "summary": text,
        }

    def _review_prompt(
        self, review_type: str, task: PlanTask, impl_summary: str,
        prev_findings: list | None, round_num: int,
    ) -> str:
        parts = [
            f"## {review_type} — {task.title}\n",
            "### Task Spec\n",
            task.full_text,
            "\n### Implementation Summary\n",
            impl_summary,
        ]
        if prev_findings and round_num > 1:
            parts += [
                f"\n### Your Previous Review (Round {round_num - 1})\n",
                json.dumps(prev_findings, indent=2),
                "\nCheck whether these findings were addressed, and look for anything new.",
            ]
        parts.append(
            '\n\nReturn JSON: {"verdict": "APPROVE"|"REVISE", "summary": "...", "findings": [...]}'
        )
        return "\n".join(parts)

    def _spec_review_prompt(self, task, impl_summary, prev_findings, round_num):
        return self._review_prompt("Spec Review", task, impl_summary, prev_findings, round_num)

    def _quality_review_prompt(self, task, impl_summary, prev_findings, round_num):
        return self._review_prompt("Code Quality Review", task, impl_summary, prev_findings, round_num)

    def _rewrite_prompt(self, task: PlanTask, aggregated_findings: str) -> str:
        return f"""## Rewrite — {task.title}

### Original Task Spec
{task.full_text}

### Findings to Address
{aggregated_findings}

Address all findings. Maintain the same commit structure as the original implementation."""
