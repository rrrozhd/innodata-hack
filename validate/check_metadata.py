"""Metadata checks: generator known, question present, tolerance stated, examples cited, hand-check."""
from __future__ import annotations

from validate import hand_check as handcheck
from generate.base import CheckResult, format_tolerance
from validate.context import CheckContext


def run(ctx: CheckContext) -> list[tuple[str, CheckResult]]:
    t = ctx.task
    out: list[tuple[str, CheckResult]] = []

    if ctx.generator is None:
        out.append(("generator_registered", CheckResult.fail(
            f"generator {t.generator!r} is not registered (file deleted or disabled?)",
            where="generate/generators/")))
    else:
        out.append(("generator_registered", CheckResult.ok(f"generator {t.generator!r} found")))

    out.append(("question_present", CheckResult.ok("question text present") if t.question.strip()
                else CheckResult.fail("question is empty", where="tasks/tasks.jsonl")))

    if t.answer_type == "number":
        if (t.tolerance is None or t.tolerance == 0) and getattr(ctx.generator, "exact_answer", False):
            out.append(("tolerance_stated", CheckResult.ok(
                "exact numeric answer (the generator declares exact_answer = True), so no tolerance is needed")))
        elif t.tolerance is None or t.tolerance == 0:
            out.append(("tolerance_stated", CheckResult.warn(
                "numeric answer with no tolerance: the model must match exactly. If the question "
                "is a visual estimate, set `tolerance` on the generator (it is then stated in the prompt).")))
        else:
            tol = format_tolerance(t.tolerance)
            if tol in t.question:
                out.append(("tolerance_stated", CheckResult.ok(f"tolerance +/-{tol} is stated in the prompt")))
            else:
                out.append(("tolerance_stated", CheckResult.fail(
                    "tolerance is used in scoring but not stated in the prompt",
                    expected=f"question contains '{tol}'", actual=t.question,
                    where="generator.make_question() / build_question()")))
    else:
        out.append(("tolerance_stated", CheckResult.skip(f"not a numeric answer ({t.answer_type})")))

    n = len([e for e in t.real_world_examples if e.strip()])
    out.append(("examples_cited", CheckResult.ok(f"{n} real-world example(s) cited") if 1 <= n <= 2
                else CheckResult.fail(f"{n} real-world examples cited; the rules ask for one or two",
                                      where="generator.real_world_examples")))

    state, entry = handcheck.status(ctx.ledger, t.id, ctx.fingerprint)
    if state == "ok":
        out.append(("hand_checked", CheckResult.ok(f"hand-checked by {entry['by']} on {entry['date']}")))
    elif state == "stale":
        out.append(("hand_checked", CheckResult.warn(
            f"hand-check by {entry['by']} is STALE: the task changed since it was checked",
            where=f"python -m validate.hand_check {t.id} --by NAME")))
    else:
        out.append(("hand_checked", CheckResult.warn(
            "not hand-checked yet (required before submitting)",
            where=f"python -m validate.hand_check {t.id} --by NAME --notes '...'")))
    return out
