import pytest
from pathlib import Path
from harness_claw.runtime.plan_parser import PlanTask, parse_plan

SAMPLE_PLAN = """\
# My Feature Plan

Some intro text here.

## Task 1: Add RoleConfig fields

Step 1: do this
Step 2: do that

## Task 2: Add Task fields

Step 1: something else
"""


def test_parse_plan_returns_correct_count(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text(SAMPLE_PLAN)
    tasks = parse_plan(str(p))
    assert len(tasks) == 2


def test_parse_plan_task_index(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text(SAMPLE_PLAN)
    tasks = parse_plan(str(p))
    assert tasks[0].index == 1
    assert tasks[1].index == 2


def test_parse_plan_task_title(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text(SAMPLE_PLAN)
    tasks = parse_plan(str(p))
    assert tasks[0].title == "Add RoleConfig fields"
    assert tasks[1].title == "Add Task fields"


def test_parse_plan_full_text_contains_steps(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text(SAMPLE_PLAN)
    tasks = parse_plan(str(p))
    assert "Step 1: do this" in tasks[0].full_text
    assert "Step 1: something else" in tasks[1].full_text


def test_parse_plan_single_task(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text("# Plan\n\n## Task 1: Only task\n\nDo the thing.")
    tasks = parse_plan(str(p))
    assert len(tasks) == 1
    assert tasks[0].title == "Only task"


def test_parse_plan_file_not_found():
    with pytest.raises(FileNotFoundError):
        parse_plan("/nonexistent/plan.md")
