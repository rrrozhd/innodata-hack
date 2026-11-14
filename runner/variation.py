"""Stage 3: summarize how tasks differ and flag near-duplicates.

Rubric: meaningful variation.
"""

from typing import Any

from runner.context import RunContext, StageResult

NAME = "Variation report"


def describe_task(task: dict[str, Any]) -> dict[str, str]:
    """Return visual type, data shape, question type, and parameters for one task.

    TODO: define how each attribute is read from the task.
    """
    raise NotImplementedError("TODO: describe task")


def find_near_duplicates(
    descriptions: dict[str, dict[str, str]],
    threshold: float,
) -> list[tuple[str, str, float]]:
    """Return pairs of task ids whose descriptions are too similar.

    TODO: choose the similarity measure and threshold.
    """
    raise NotImplementedError("TODO: find near-duplicates")


def run(ctx: RunContext) -> StageResult:
    """Print the variation summary and any near-duplicate warnings.

    TODO: print counts per attribute and warn on near-duplicates.
    """
    raise NotImplementedError("TODO: variation report")
