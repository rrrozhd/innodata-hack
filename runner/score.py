"""Stage 5: score answers and report per-model accuracy and task difficulty.

Rubric: task difficulty.
Flags tasks both models solve (too easy) and tasks neither solves (check fairness).
"""

from typing import Any

from runner.context import RunContext, StageResult

NAME = "Scoring and difficulty"


def score_answer(extracted: str | None, reference: Any, tolerance: float | None) -> bool:
    """Return True if the extracted answer matches the reference within tolerance.

    TODO: implement the comparison, including the tolerance rule from check (f).
    """
    raise NotImplementedError("TODO: score answer")


def per_model_accuracy(responses: list[dict[str, Any]]) -> dict[str, float]:
    """Return accuracy per model.

    TODO: implement.
    """
    raise NotImplementedError("TODO: per-model accuracy")


def classify_tasks(responses: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Return task ids grouped as too_easy (both pass), unsolved (both fail), and mixed.

    TODO: implement.
    """
    raise NotImplementedError("TODO: classify tasks by difficulty")


def run(ctx: RunContext) -> StageResult:
    """Print per-model accuracy, per-task pass/fail, and difficulty groups.

    TODO: implement the report.
    """
    raise NotImplementedError("TODO: scoring and difficulty report")
