"""Stage 5: score answers and report per-model accuracy and task difficulty.

Rubric: task difficulty.
Flags tasks both models solve (too easy) and tasks neither solves (check fairness).
Reads saved replies from results/raw/ when stage 4 was not run in this invocation (no API calls).
"""
from __future__ import annotations

from typing import Any

from runner.context import RunContext, StageAbort, StageResult

NAME = "Scoring and difficulty"


def score_answer(extracted: str | None, reference: Any, tolerance: float | None,
                 answer_type: str | None = None) -> bool:
    """True if the extracted answer matches the reference (numbers within tolerance)."""
    from eval.score import score
    return score(extracted, reference, tolerance, answer_type)


def per_model_accuracy(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Accuracy per model over VALID replies only (invalid replies are not scored)."""
    right: dict[str, int] = {}
    total: dict[str, int] = {}
    for row in rows:
        if row["status"] != "ok":
            continue
        total[row["model_label"]] = total.get(row["model_label"], 0) + 1
        right[row["model_label"]] = right.get(row["model_label"], 0) + (row["correct"] == "true")
    return {m: right.get(m, 0) / n for m, n in total.items()}


def classify_tasks(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Task ids grouped as too_easy (all models right), unsolved (all wrong), mixed, incomplete."""
    by_task: dict[str, list[dict]] = {}
    for row in rows:
        by_task.setdefault(row["task_id"], []).append(row)
    groups: dict[str, list[str]] = {"too_easy": [], "unsolved": [], "mixed": [], "incomplete": []}
    for tid, task_rows in by_task.items():
        if any(r["status"] != "ok" for r in task_rows):
            groups["incomplete"].append(tid)
            continue
        flags = [r["correct"] == "true" for r in task_rows]
        groups["too_easy" if all(flags) else "unsolved" if not any(flags) else "mixed"].append(tid)
    return groups


def run(ctx: RunContext) -> StageResult:
    """Print per-model accuracy, per-task pass/fail, and difficulty groups; write results/responses.csv."""
    from eval import call_models, report, score
    out = ctx.out
    validation = ctx.ensure_validation()
    tasks = [t for t in ctx.selected_tasks() if t.id in validation.valid_ids]
    if not tasks:
        raise StageAbort("no valid tasks to score")
    models = ctx.models()
    if ctx.records is None:
        ctx.records = call_models.load_records(ctx.cfg, tasks, models)
        out.info("read saved replies from results/raw/ (no API calls)")
    ctx.rows = score.build_rows(tasks, ctx.records)
    labels = [m.label for m in models]
    ran = bool(ctx.rows) and not all(r["status"] == "not_run" for r in ctx.rows)
    partial = bool(ctx.task_filter) or bool(getattr(ctx.args, "models", None))
    if ran and partial:
        out.info("--task/--models limits this run, so results/responses.csv and summary.md are NOT rewritten "
                 "(they hold the full set); run stage 5 without those flags to refresh them")
        ran = False
    if ran:
        score.write_csv(ctx.cfg.results_dir / "responses.csv", ctx.rows)
        ctx.files_written.append("results/responses.csv")
        out.info("wrote results/responses.csv")
    ctx.scoring = report.print_report(ctx.cfg, out, tasks, ctx.rows, labels)
    if ran:
        ctx.files_written += report.write_summary_files(ctx.cfg, ctx.scoring, tasks, labels)
    return ctx.stage_result(NAME)
