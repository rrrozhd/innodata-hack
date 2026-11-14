"""Answer check: recompute the reference answer in code from the SAVED data and compare."""
from __future__ import annotations

from eval.score import answers_equal
from generate.base import CheckResult
from validate.context import CheckContext


def run(ctx: CheckContext) -> list[tuple[str, CheckResult]]:
    ctx.load_data()
    if ctx.generator is None or ctx.data_error:
        return [("answer_recomputes", CheckResult.skip("needs the generator and a loadable data file"))]
    try:
        fresh = ctx.generator.coerce_answer(ctx.generator.compute_answer(ctx.data))
    except Exception as exc:  # noqa: BLE001
        return [("answer_recomputes", CheckResult.fail(
            f"compute_answer() raised {type(exc).__name__}: {exc}", where="generator.compute_answer()"))]
    if answers_equal(ctx.task.answer_type, ctx.task.answer, fresh):
        return [("answer_recomputes", CheckResult.ok(
            f"recomputed answer matches the stored answer ({ctx.task.answer!r})"))]
    return [("answer_recomputes", CheckResult.fail(
        "stored answer differs from the answer recomputed from the data",
        expected=fresh, actual=ctx.task.answer, where="tasks/tasks.jsonl vs generator.compute_answer()"))]


def compute_reference_answer(task):
    """Return the reference answer computed in code from the task's SAVED data file.

    `task` is a record in the repo schema (dict) or a Task. Raises ValueError if it cannot be computed.
    """
    from validate.context import legacy_context
    ctx = legacy_context(task)
    if ctx.generator is None or ctx.data_error:
        raise ValueError(ctx.data_error or f"generator {ctx.task.generator!r} is not registered")
    return ctx.generator.compute_answer(ctx.data)
