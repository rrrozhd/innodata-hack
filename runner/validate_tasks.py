"""Stage 2: per-task validation, one colored result line per check.

Rubric evidence:
  a. structured data loads ............ reliable generation
  b. image renders from the data ...... reliable generation
  c. reference answer recomputed ...... fair and verifiable tasks
  d. rendering matches the data ....... fair and verifiable tasks
  e. question requires the image ...... fair and verifiable tasks
  f. numeric tolerance stated and used  fair and verifiable tasks
  g. real-world examples cited ........ realistic and useful visuals
  h. marked hand-checked .............. realistic and useful visuals
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from runner.context import RunContext, StageResult

NAME = "Per-task validation"


@dataclass
class CheckResult:
    passed: bool
    expected: str = ""
    actual: str = ""
    where: str = ""


def check_data_loads(task: dict[str, Any], ctx: RunContext) -> CheckResult:
    """Check (a): the structured data exists and loads. TODO."""
    raise NotImplementedError("TODO: check (a) data loads")


def check_image_renders(task: dict[str, Any], ctx: RunContext) -> CheckResult:
    """Check (b): the image renders from the data. TODO."""
    raise NotImplementedError("TODO: check (b) image renders")


def check_reference_matches(task: dict[str, Any], ctx: RunContext) -> CheckResult:
    """Check (c): the reference answer recomputed in code matches the stored one. TODO."""
    raise NotImplementedError("TODO: check (c) reference answer")


def check_render_matches_data(task: dict[str, Any], ctx: RunContext) -> CheckResult:
    """Check (d): the rendering matches the data. TODO."""
    raise NotImplementedError("TODO: check (d) render matches data")


def check_needs_image(task: dict[str, Any], ctx: RunContext) -> CheckResult:
    """Check (e): the question cannot be answered from text alone. TODO."""
    raise NotImplementedError("TODO: check (e) needs image")


def check_tolerance_stated(task: dict[str, Any], ctx: RunContext) -> CheckResult:
    """Check (f): numeric tolerance is stated in the prompt and used in scoring. TODO."""
    raise NotImplementedError("TODO: check (f) tolerance")


def check_examples_cited(task: dict[str, Any], ctx: RunContext) -> CheckResult:
    """Check (g): one to two real-world examples are cited. TODO."""
    raise NotImplementedError("TODO: check (g) examples cited")


def check_hand_checked(task: dict[str, Any], ctx: RunContext) -> CheckResult:
    """Check (h): the task is marked hand-checked. TODO."""
    raise NotImplementedError("TODO: check (h) hand-checked")


CHECKS: list[tuple[str, str, Callable[[dict[str, Any], RunContext], CheckResult]]] = [
    ("a", "data loads", check_data_loads),
    ("b", "image renders", check_image_renders),
    ("c", "reference matches", check_reference_matches),
    ("d", "render matches data", check_render_matches_data),
    ("e", "needs image", check_needs_image),
    ("f", "tolerance stated", check_tolerance_stated),
    ("g", "examples cited", check_examples_cited),
    ("h", "hand-checked", check_hand_checked),
]


def run_task_checks(task: dict[str, Any], ctx: RunContext) -> dict[str, CheckResult]:
    """Run every check on one task and return the results by letter.

    TODO: print each result line and collect failures.
    """
    raise NotImplementedError("TODO: run checks for one task")


def run(ctx: RunContext) -> StageResult:
    """Validate every task in ctx.tasks (or only ctx.task_filter) and fill ctx.valid_task_ids.

    TODO: implement the loop and summary counts.
    """
    raise NotImplementedError("TODO: validate tasks")
