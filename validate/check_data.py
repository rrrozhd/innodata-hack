"""Data checks: the saved data loads, and regenerating it from the seed gives the same thing."""
from __future__ import annotations

import random

from generate.base import CheckResult, to_jsonable
from validate.context import CheckContext


def run(ctx: CheckContext) -> list[tuple[str, CheckResult]]:
    ctx.load_data()
    if ctx.data_error:
        return [("data_loads", CheckResult.fail(ctx.data_error, where=ctx.task.data_path))]
    out = [("data_loads", CheckResult.ok(f"data file loads ({ctx.task.data_path})"))]
    if not ctx.data:
        out.append(("data_nonempty", CheckResult.fail("data file is empty", where=ctx.task.data_path)))
    else:
        out.append(("data_nonempty", CheckResult.ok("data is non-empty")))

    if ctx.generator is None:
        out.append(("data_reproducible", CheckResult.skip("generator missing; cannot regenerate")))
        return out
    try:
        regenerated = to_jsonable(ctx.generator.generate_data(random.Random(ctx.task.seed), ctx.task.index))
    except Exception as exc:  # noqa: BLE001
        out.append(("data_reproducible", CheckResult.fail(
            f"generate_data() raised {type(exc).__name__}: {exc}", where="generator.generate_data()")))
        return out
    if regenerated == ctx.data:
        out.append(("data_reproducible", CheckResult.ok("regenerating from the seed gives identical data")))
    else:
        out.append(("data_reproducible", CheckResult.fail(
            "regenerating from the same seed gives DIFFERENT data (non-deterministic generator)",
            expected="identical to the saved file", actual="differs",
            where="generator.generate_data(): use only the `rng` argument; no clock/global random")))
    return out
