"""Stage 1: parse tasks/tasks.jsonl, count tasks, and flag schema problems.

Rubric: reliable generation.
The placeholder record (`"_placeholder": true`) is skipped and never counted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runner.context import RunContext, StageAbort, StageResult
from runner.schema import load_tasks

NAME = "Load tasks"
TASKS_PATH = Path("tasks/tasks.jsonl")
REQUIRED_FIELDS = ["task_id", "image_path", "question", "data", "reference_answer", "checks"]


def parse_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (records, error messages) for a JSONL file; errors carry line numbers.

    Placeholder records are left out of the result.
    """
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    if not path.exists():
        return [], [f"{path} does not exist"]
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {lineno}: invalid JSON ({exc})")
            continue
        if not isinstance(rec, dict):
            errors.append(f"line {lineno}: each line must be a JSON object")
        elif not rec.get("_placeholder"):
            records.append(rec)
    return records, errors


def find_schema_problems(record: dict[str, Any]) -> list[str]:
    """Return the required fields (repo schema) that are missing from one record."""
    return [f"missing field: {name}" for name in REQUIRED_FIELDS if name not in record]


def run(ctx: RunContext) -> StageResult:
    """Load tasks into ctx.tasks and print the count and any problems."""
    out, cfg = ctx.out, ctx.cfg
    rel = cfg.tasks_file.relative_to(cfg.root)
    tasks, problems = load_tasks(cfg.tasks_file)
    ctx.tasks = tasks
    if cfg.tasks_file.exists():  # a missing file is explained once, below
        for problem in problems:
            out.fail(problem)
    if not tasks:
        raw, _ = parse_jsonl(cfg.tasks_file)
        if cfg.tasks_file.exists() and not raw and not problems:
            out.fail(f"{rel} only holds the placeholder record: no real tasks yet.")
        else:
            out.fail(f"no valid tasks in {rel}.")
        out.detail("Generate them first: python -m generate.generate_tasks   (generators go in generate/generators/)")
        raise StageAbort("nothing to validate or evaluate yet")
    out.pass_(f"loaded {len(tasks)} task(s) from {rel}")
    out.table(["id", "generator", "visual type", "question type", "answer"],
              [[t.id, t.generator, t.visual_type, t.question_type,
                f"{t.answer_type}" + (f" +/-{t.tolerance:g}" if t.tolerance else "")] for t in tasks])
    target = cfg.get("task_count_target", 10)
    if len(tasks) == target:
        out.pass_(f"task count matches the target ({target})")
    else:
        out.warn(f"{len(tasks)} task(s) loaded but the brief asks for {target}")
    return ctx.stage_result(NAME)
