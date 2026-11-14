"""Blind baseline: ask the models the question with no image, and with a blank
image of the same size. If a model still answers correctly, the question does
not need the image.

    python -m eval.blind_baseline --tasks tasks/final --results results/final

Output goes to blind_baseline.csv in the results folder (a separate file, so it
is never mistaken for the reported responses).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

from PIL import Image

from factory.build import load_task_set

from .client import Settings, complete
from .extract import final_answer
from .score import score


def blank_like(png: bytes) -> bytes:
    im = Image.open(io.BytesIO(png))
    buf = io.BytesIO()
    Image.new("RGB", im.size, (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def run(tasks_dir, results_dir, models, settings: Settings) -> list:
    tasks_dir, results_dir = Path(tasks_dir), Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for model in models:
        for rec, data in load_task_set(tasks_dir):
            png = (tasks_dir / rec["image"]).read_bytes()
            for condition, image in (("no_image", None), ("blank_image", blank_like(png))):
                reply = complete(model, rec, data, image, settings)
                parsed = final_answer(reply.content)
                ok, _ = score(parsed, rec)
                rows.append({"model": model, "task_id": rec["task_id"], "condition": condition,
                             "finish_reason": reply.finish_reason, "parsed": parsed, "correct": ok,
                             "reply": reply.content})
    with open(results_dir / "blind_baseline.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    summary = {}
    for r in rows:
        key = f"{r['model']} / {r['condition']}"
        summary.setdefault(key, [0, 0])
        summary[key][0] += r["correct"]
        summary[key][1] += 1
    (results_dir / "blind_baseline_summary.json").write_text(json.dumps(summary, indent=1) + "\n",
                                                             encoding="utf-8", newline="\n")
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--models", default="anthropic/claude-haiku-5.5,openai/gpt-6-luna")
    ap.add_argument("--base-url", default=Settings.base_url)
    ap.add_argument("--key-env", default=Settings.key_env)
    a = ap.parse_args(argv)
    rows = run(a.tasks, a.results, a.models.split(","), Settings(base_url=a.base_url, key_env=a.key_env))
    right = sum(r["correct"] for r in rows)
    print(f"{right} of {len(rows)} blind answers were correct; anything above chance means the question leaks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
