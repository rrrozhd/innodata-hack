"""Stage 4: run both models on every valid task.

Rubric: model evaluation.
Settings come from results/run_config.json (temperature 0, reasoning high, fixed token limit).
A model whose openrouter_slug is still a PLACEHOLDER cannot be called live; --dry-run still works.
Invalid replies (API error, token cutoff, empty, filtered) are saved but never scored and never cached,
so running this stage again re-runs exactly those. Valid replies are reused (no second API call).
Dry-run makes no API calls.
"""
from __future__ import annotations

import json

from runner.context import RunContext, StageAbort, StageResult

NAME = "Model evaluation"
MAX_RETRIES = 2
INVALID_STATUSES = ("api_error", "truncated", "empty", "filtered")


def is_invalid_reply(reply) -> str | None:
    """The invalid reason (api_error, truncated, empty, filtered) for a reply, or None if it is valid.

    `reply` is a status string or a dict with a "status" key (the saved raw reply)."""
    status = reply.get("status") if isinstance(reply, dict) else reply
    return status if status in INVALID_STATUSES else None


def _task_and_model(task_id: str, model: str, ctx: RunContext):
    task = next((t for t in ctx.ensure_tasks() if t.id == task_id), None)
    spec = next((m for m in ctx.cfg.models() if m.label == model), None)
    if task is None or spec is None:
        raise ValueError(f"unknown task {task_id!r} or model {model!r}")
    return task, spec


def call_model(task_id: str, model: str, ctx: RunContext) -> dict:
    """Send one task to one model (always a fresh call, saved under results/raw/) and return the saved reply.

    Skipped (returns a not_run reply) when ctx.dry_run is True. Placeholder slugs raise ValueError."""
    from eval import call_models
    task, spec = _task_and_model(task_id, model, ctx)
    if ctx.dry_run:
        return {"task_id": task_id, "model_label": model, "status": "not_run"}
    if spec.is_placeholder:
        raise ValueError(f"model slug for {model!r} is still a placeholder")
    records, _ = call_models.run_evaluation(ctx.cfg, ctx.out, [task], ctx.get_client(), [spec], force=True)
    return json.loads(call_models.raw_file(ctx.cfg, spec, task_id).read_text(encoding="utf-8"))


def call_with_retries(task_id: str, model: str, ctx: RunContext) -> dict:
    """Call the model, rerunning up to MAX_RETRIES times while the reply is invalid (never scored)."""
    reply = call_model(task_id, model, ctx)
    for _ in range(MAX_RETRIES):
        if ctx.dry_run or is_invalid_reply(reply) is None:
            break
        reply = call_model(task_id, model, ctx)
    return reply


def save_raw(task_id: str, model: str, reply: dict, ctx: RunContext) -> str:
    """Write one raw reply under results/raw/<model>/ and return its path (relative to the repo root)."""
    from eval import call_models
    path = call_models.save_raw_response(task_id, model, reply, root=ctx.cfg.root)
    return str(path.relative_to(ctx.cfg.root))


def run(ctx: RunContext) -> StageResult:
    """Run every valid task against every model, printing progress and the running cost."""
    from eval import call_models
    out = ctx.out
    validation = ctx.ensure_validation()
    selected = ctx.selected_tasks()
    tasks = [t for t in selected if t.id in validation.valid_ids]
    if not tasks:
        raise StageAbort("no valid tasks to evaluate (fix the validation failures first)")
    skipped = [t for t in selected if t.id not in validation.valid_ids]
    if skipped:
        out.warn(f"{len(skipped)} invalid task(s) excluded: " + ", ".join(t.id for t in skipped))
    models = ctx.models()
    if not models:
        raise StageAbort("no models selected (check --models and results/run_config.json)")
    placeholders = [m.label for m in models if m.is_placeholder]
    if placeholders and not ctx.dry_run:
        raise StageAbort("model slug(s) not set for " + ", ".join(placeholders)
                         + ". Put the confirmed OpenRouter slugs in results/run_config.json -> models[].openrouter_slug")
    client = None if ctx.dry_run else ctx.get_client()
    if ctx.dry_run:
        out.info("DRY RUN: no API calls will be made and no budget is spent")
    ctx.records, stats = call_models.run_evaluation(ctx.cfg, out, tasks, client, models,
                                                    force=getattr(ctx.args, "force", False), dry_run=ctx.dry_run)
    ctx.usage.add(0, 0, stats.get("spent", 0.0))
    if not ctx.dry_run and stats.get("calls"):
        ctx.files_written += ["results/run_config.json (run record updated)", "results/raw/"]
    return ctx.stage_result(NAME)
