"""Run the validity checks (steps 3-5) over tasks/tasks.jsonl.

Run from the repo root with: python -m validate.run_all

Placeholder only. The output format and failure handling are not decided yet.
"""

import json
from pathlib import Path
from typing import Any

from validate.check_answer import compute_reference_answer
from validate.check_needs_image import needs_image
from validate.check_render import check_render

TASKS_PATH = Path("tasks/tasks.jsonl")


def load_tasks(path: Path = TASKS_PATH) -> list[dict[str, Any]]:
    """Load tasks from a JSONL file.

    TODO: skip records marked "_placeholder": true once real tasks exist.
    """
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def run_checks(task: dict[str, Any]) -> dict[str, Any]:
    """Run all validity checks on one task and return the results.

    TODO: decide the result schema and how failures are reported.
    """
    raise NotImplementedError("TODO: run checks for one task")


def main() -> None:
    """Validate every task and print a summary.

    TODO: implement the summary output.
    """
    raise NotImplementedError("TODO: validate all tasks")


if __name__ == "__main__":
    main()
