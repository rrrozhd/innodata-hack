"""Stage 2: per-task validation, one colored result line per check, then a rubric-letter summary.

Rubric evidence (each check line is tagged with its letter):
  a. structured data loads ............ reliable generation
  b. image renders from the data ...... reliable generation
  c. reference answer recomputed ...... fair and verifiable tasks
  d. rendering matches the data ....... fair and verifiable tasks
  e. question requires the image ...... fair and verifiable tasks
  f. numeric tolerance stated and used  fair and verifiable tasks
  g. real-world examples cited ........ realistic and useful visuals
  h. marked hand-checked .............. realistic and useful visuals

The checks themselves live in validate/ (check_data, check_render, check_answer, check_needs_image,
check_metadata); validate/run_all.py runs them and tags each with its letter.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from runner.context import RunContext, StageAbort, StageResult
from validate.run_all import RUBRIC_LABELS, RUBRIC_LETTERS, validate_tasks

NAME = "Per-task validation"


@dataclass
class CheckResult:
    """Outcome of one rubric check (a-h) for one task."""
    passed: bool
    expected: str = ""
    actual: str = ""
    where: str = ""


def _letter_check(letter: str) -> Callable[[Any, RunContext], CheckResult]:
    names = [n for n, l in RUBRIC_LETTERS.items() if l == letter]

    def check(task: Any, ctx: RunContext) -> CheckResult:
        """Run the full validation for this one task (quietly) and fold its checks for this letter."""
        with ctx.out.quiet():
            report = validate_tasks(ctx.cfg, ctx.out, [task], ctx.registry, strict=ctx.strict)
        mine = [(n, r) for n, r in report.results[task.id] if n in names]
        bad = [(n, r) for n, r in mine if r.status in ("fail", "warn")]
        if not bad:
            return CheckResult(True)
        n, r = bad[0]
        return CheckResult(False, str(r.expected or ""), str(r.actual or r.message), r.where)
    check.__name__ = f"check_{letter}"
    return check


# (letter, short label, function(task, ctx) -> CheckResult), in rubric order a-h
CHECKS: list[tuple[str, str, Callable[[Any, RunContext], CheckResult]]] = [
    (letter, label, _letter_check(letter)) for letter, label in RUBRIC_LABELS.items()]


def run_task_checks(task: Any, ctx: RunContext) -> dict[str, CheckResult]:
    """Run every rubric check (a-h) on one Task and return the results by letter."""
    return {letter: fn(task, ctx) for letter, _, fn in CHECKS}


def run(ctx: RunContext) -> StageResult:
    """Validate every task in ctx.tasks (or only --task) and fill ctx.validation.valid_ids."""
    out = ctx.out
    tasks = ctx.selected_tasks()
    if not tasks:
        raise StageAbort("no tasks loaded; run the generators first (python -m generate.generate_tasks)")
    client = baseline_model = None
    if getattr(ctx.args, "text_only_baseline", False) or ctx.cfg.get("validation.text_only_baseline"):
        if ctx.dry_run:
            out.info("text-only baseline skipped (--dry-run makes no API calls)")
        else:
            models = ctx.models()
            if not models or models[0].is_placeholder:
                out.warn("text-only baseline skipped: model slugs are not set in results/run_config.json")
            else:
                client, baseline_model = ctx.get_client(), models[0]
                out.info(f"text-only baseline ON: asking {baseline_model.label} each question WITHOUT the "
                         "image (uses API budget)")
    ctx.validation = report = validate_tasks(ctx.cfg, out, tasks, ctx.registry, strict=ctx.strict,
                                             client=client, baseline_model=baseline_model)
    out.blank()
    out.heading("Rubric checks (tasks passing every check with that letter)")
    summary = report.letter_summary()
    for letter, label in RUBRIC_LABELS.items():
        c = summary[letter]
        out.status("pass" if c["pass"] == c["total"] else "warn", f"({letter}) {label}: {c['pass']}/{c['total']}", indent=2)
    out.blank()
    out.info(f"{len(report.valid_ids)} valid, {len(report.invalid_ids)} invalid. Evaluation only runs on valid "
             "tasks. Details saved to results/validation.json")
    ctx.files_written.append("results/validation.json")
    return ctx.stage_result(NAME)
