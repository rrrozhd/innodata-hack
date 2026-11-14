"""Run models on a task folder, score the replies and write the results folder.

    python -m eval.run_eval --tasks tasks/final --results results/final \
        --models anthropic/claude-haiku-5.5,openai/gpt-6-luna

Writes responses.csv (or responses.jsonl when a reply is too long for the
judges' CSV reader), run_config.json, one raw reply file per call, and a summary.
A run counts as complete only if every reply finished with finish_reason "stop".
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from factory import GENERATOR_VERSION
from factory.build import load_task_set

from . import report
from .client import Settings, complete, cost_usd, is_mock
from .extract import final_answer
from .fingerprint import label
from .prompt import PROMPT_TEMPLATE, SYSTEM_PROMPT
from .score import score

REQUIRED = ["model", "task_id", "status", "parsed", "question_type", "response", "ref_answer", "correct",
            "finish_reason", "error"]
EXTRA = ["fingerprint", "fingerprint_kind", "family", "level", "variant", "latency_s", "prompt_tokens",
         "completion_tokens", "cost_usd"]
CSV_CELL_LIMIT = 120000  # the judges' CSV reader stops at 131072 characters
DEFAULT_MODELS = ["anthropic/claude-haiku-5.5", "openai/gpt-6-luna"]


def slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9.\-]+", "_", model)


def _row(model: str, record: dict, data: dict, reply) -> dict:
    stop = reply.status == "ok" and reply.finish_reason == "stop"
    if reply.status != "ok":
        status, error = "api_error", "api_error"
    elif reply.finish_reason == "length":
        status, error = "ok", "length"
    elif not stop:  # any other finish reason is an incomplete reply
        status, error = "api_error", "api_error"
    else:
        status, error = "ok", ""
    parsed_text = final_answer(reply.content) if stop else ""
    correct, parsed = score(parsed_text, record) if stop else (0, None)
    fp_id, fp_kind = label(record, data, parsed, correct) if stop else ("", "")
    ref = record["answer"]
    return {
        "model": model, "task_id": record["task_id"], "status": status, "parsed": parsed_text,
        "question_type": record["question_type"], "response": reply.content,
        "ref_answer": json.dumps(ref) if isinstance(ref, list) else str(ref),
        "correct": "" if error else str(correct), "finish_reason": reply.finish_reason, "error": error,
        "fingerprint": fp_id, "fingerprint_kind": fp_kind, "family": record.get("family", ""),
        "level": record.get("difficulty", {}).get("level", ""),
        "variant": record.get("difficulty", {}).get("variant", ""), "latency_s": reply.latency_s,
        "prompt_tokens": reply.usage.get("prompt_tokens", ""),
        "completion_tokens": reply.usage.get("completion_tokens", ""),
        "cost_usd": cost_usd(model, reply.usage) if not is_mock(model) else 0,
    }


def evaluate(tasks_dir, results_dir, models, settings: Settings | None = None, workers: int = 4,
             with_image: bool = True) -> dict:
    settings = settings or Settings()
    tasks_dir, results_dir = Path(tasks_dir), Path(results_dir)
    (results_dir / "raw").mkdir(parents=True, exist_ok=True)
    tasks = load_task_set(tasks_dir)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows, retry_log = [], []

    def one(job):
        model, record, data = job
        png = (tasks_dir / record["image"]).read_bytes() if with_image else None
        reply = complete(model, record, data, png, settings)
        raw = {"model": model, "task_id": record["task_id"], "status": reply.status,
               "finish_reason": reply.finish_reason, "error_detail": reply.error_detail,
               "attempts": reply.attempts, "latency_s": reply.latency_s, "usage": reply.usage,
               "content": reply.content, "response_body": reply.raw}
        (results_dir / "raw" / f"{slug(model)}__{record['task_id']}.json").write_text(
            json.dumps(raw, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return _row(model, record, data, reply), [dict(r, model=model, task_id=record["task_id"])
                                                  for r in reply.retries]

    jobs = [(m, rec, data) for m in models for rec, data in tasks]
    real = any(not is_mock(m) for m in models)
    with ThreadPoolExecutor(max_workers=workers if real else 1) as pool:
        for row, retries in pool.map(one, jobs):
            rows.append(row)
            retry_log += retries

    incomplete = [f"{r['model']} / {r['task_id']}: {r['error'] or r['finish_reason'] or 'no finish reason'}"
                  for r in rows if not (r["status"] == "ok" and r["finish_reason"] == "stop" and not r["error"])]
    for name in ("responses.csv", "responses.jsonl"):
        (results_dir / name).unlink(missing_ok=True)
    longest = max((len(str(v)) for r in rows for v in r.values()), default=0)
    if longest > CSV_CELL_LIMIT:
        (results_dir / "responses.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="\n")
        results_file = "responses.jsonl"
    else:
        with open(results_dir / "responses.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=REQUIRED + EXTRA)
            w.writeheader()
            w.writerows(rows)
        results_file = "responses.csv"

    config = {
        "models": list(models), "temperature": settings.temperature, "max_tokens": settings.max_tokens,
        "reasoning": {"effort": settings.reasoning_effort}, "prompt_template": PROMPT_TEMPLATE,
        "prompt_system": SYSTEM_PROMPT, "prompt_note": "The task's question field is the complete text sent; "
                                                      "the image is attached as a base64 PNG data URL.",
        "base_url": settings.base_url, "with_image": with_image, "generator_version": GENERATOR_VERSION,
        "tasks_folder": str(tasks_dir).replace("\\", "/"), "n_tasks": len(tasks), "results_file": results_file,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "complete": not incomplete, "incomplete_rows": incomplete,
        "placeholder": any(is_mock(m) for m in models),
        "placeholder_note": ("Produced with offline mock backends to exercise the pipeline. NOT model results."
                             if any(is_mock(m) for m in models) else ""),
        "sampling_note": "temperature 0 is sent as the brief requires. OpenRouter's model list does not include "
                         "temperature among the supported parameters of either evaluated model, so sending it "
                         "is not evidence that it was applied.",
        "total_cost_usd": round(sum(float(r["cost_usd"] or 0) for r in rows), 4),
    }
    (results_dir / "run_config.json").write_text(json.dumps(config, indent=1) + "\n", encoding="utf-8",
                                                 newline="\n")
    (results_dir / "api_retry_log.json").write_text(json.dumps(retry_log, indent=1) + "\n", encoding="utf-8",
                                                    newline="\n")
    summary = report.summarise(rows, tasks, config)
    (results_dir / "summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8",
                                              newline="\n")
    (results_dir / "summary.md").write_text(report.markdown(summary), encoding="utf-8", newline="\n")
    return {"rows": rows, "config": config, "summary": summary}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate models on a task folder and score the replies.")
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS), help="comma-separated model IDs")
    ap.add_argument("--base-url", default=Settings.base_url, help="OpenAI-compatible endpoint")
    ap.add_argument("--key-env", default=Settings.key_env, help="environment variable holding the API key")
    ap.add_argument("--max-tokens", type=int, default=Settings.max_tokens)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    out = evaluate(a.tasks, a.results, [m for m in a.models.split(",") if m],
                   Settings(base_url=a.base_url, key_env=a.key_env, max_tokens=a.max_tokens), a.workers)
    print(report.markdown(out["summary"]))
    if not out["config"]["complete"]:
        print("RUN INCOMPLETE: " + "; ".join(out["config"]["incomplete_rows"][:5]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
