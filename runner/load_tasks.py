"""Stage 1: parse tasks/tasks.jsonl, count tasks, and flag schema problems.

Rubric: reliable generation.
"""

from pathlib import Path
from typing import Any

from runner.context import RunContext, StageResult

NAME = "Load tasks"
TASKS_PATH = Path("tasks/tasks.jsonl")
REQUIRED_FIELDS = ["task_id", "image_path", "question", "data", "reference_answer", "checks"]


def parse_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (records, error messages) for a JSONL file.

    TODO: report line numbers for malformed lines.
    """
    raise NotImplementedError("TODO: parse JSONL")


def find_schema_problems(record: dict[str, Any]) -> list[str]:
    """Return a list of missing or wrong-typed fields for one record.

    TODO: confirm the official schema before enforcing types.
    """
    raise NotImplementedError("TODO: check schema")


def run(ctx: RunContext) -> StageResult:
    """Load tasks into ctx.tasks and print the count and any problems.

    TODO: skip records marked "_placeholder": true.
    """
    raise NotImplementedError("TODO: load tasks")
