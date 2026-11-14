"""Stage 6: scoring and difficulty report (rubric: task difficulty, model evaluation)."""
from __future__ import annotations

import datetime as dt
import json

from runner.config import Config
from runner.schema import Task
from runner.output import UI
from pathlib import Path

RESPONSES_PATH = Path(__file__).resolve().parent.parent / "results" / "responses.csv"


def summarize(tasks: list[Task], rows: list[dict], model_labels: list[str]) -> dict:
    """Pure data summary (also used by the scorecard)."""
    per_model = {}
    for label in model_labels:
        mine = [r for r in rows if r["model_label"] == label]
        ok = [r for r in mine if r["status"] == "ok"]
        right = [r for r in ok if r["correct"] == "true"]
        per_model[label] = {
            "responses": len(mine), "valid": len(ok),
            "invalid": len([r for r in mine if r["status"] not in ("ok", "not_run")]),
            "not_run": len([r for r in mine if r["status"] == "not_run"]),
            "correct": len(right), "accuracy": (len(right) / len(ok)) if ok else None,
            "prompt_tokens": sum(int(r["prompt_tokens"] or 0) for r in mine),
            "completion_tokens": sum(int(r["completion_tokens"] or 0) for r in mine),
            "cost_usd": sum(float(r["cost_usd"] or 0) for r in mine),
        }
    task_class = {}
    for task in tasks:
        cells = {}
        for label in model_labels:
            r = next((x for x in rows if x["task_id"] == task.id and x["model_label"] == label), None)
            if r is None or r["status"] == "not_run":
                cells[label] = "not_run"
            elif r["status"] != "ok":
                cells[label] = "invalid"
            else:
                cells[label] = "ok" if r["correct"] == "true" else "wrong"
        scored = [v for v in cells.values() if v in ("ok", "wrong")]
        if len(scored) < len(model_labels):
            verdict = "incomplete"
        elif all(v == "ok" for v in scored):
            verdict = "both_pass"
        elif all(v == "wrong" for v in scored):
            verdict = "both_fail"
        else:
            verdict = "split"
        task_class[task.id] = {"cells": cells, "verdict": verdict}
    counts = {v: sum(1 for c in task_class.values() if c["verdict"] == v)
              for v in ("both_pass", "split", "both_fail", "incomplete")}
    return {"per_model": per_model, "tasks": task_class, "counts": counts}


def print_report(cfg: Config, ui: UI, tasks: list[Task], rows: list[dict], model_labels: list[str]) -> dict:
    summary = summarize(tasks, rows, model_labels)
    if not rows or all(r["status"] == "not_run" for r in rows):
        ui.warn("no model responses to score yet. Run the evaluate stage first "
                "(python run_tests.py --stage 4).")
        return summary

    ui.heading("Per-model results")
    table_rows = []
    for label, m in summary["per_model"].items():
        acc = "n/a" if m["accuracy"] is None else f"{m['accuracy']:.0%}"
        table_rows.append([label, f"{m['correct']}/{m['valid']}", acc, str(m["invalid"]),
                           f"{m['prompt_tokens']}/{m['completion_tokens']}", f"${m['cost_usd']:.4f}"])
    ui.table(["model", "correct/valid", "accuracy", "invalid", "tokens in/out", "cost"], table_rows)
    ui.blank()

    ui.heading("Per-task matrix (difficulty)")
    matrix = []
    for task in tasks:
        info = summary["tasks"][task.id]
        cells = []
        for label in model_labels:
            v = info["cells"][label]
            cells.append(ui.c("correct", "green") if v == "ok" else ui.c("WRONG", "red") if v == "wrong"
                         else ui.c("INVALID", "yellow") if v == "invalid" else ui.c("not run", "gray"))
        verdict = {"both_pass": ui.c("both pass", "gray"), "split": ui.c("split (good signal)", "green"),
                   "both_fail": ui.c("both fail (check fairness)", "yellow"),
                   "incomplete": ui.c("incomplete", "yellow")}[info["verdict"]]
        matrix.append([task.id, *cells, verdict])
    ui.table(["task", *model_labels, "verdict"], matrix)
    ui.blank()

    # Invalid responses: never accepted
    invalid = [r for r in rows if r["status"] not in ("ok", "not_run")]
    for r in invalid:
        ui.fail(f"INVALID reply (not accepted): {r['model_label']} / {r['task_id']} -> status '{r['status']}'")
        ui.detail(f"fix: re-run evaluate (see the evaluate stage output for the specific fix); raw: {r['raw_path']}")
    not_run = [r for r in rows if r["status"] == "not_run"]
    if not_run:
        ui.warn(f"{len(not_run)} (task, model) pair(s) have no response yet; both models must run on every valid task")

    # Wrong answers: show why
    wrong = [r for r in rows if r["status"] == "ok" and r["correct"] == "false"]
    if wrong:
        # A wrong model answer is DATA about task difficulty, not a problem with the shell, so it is
        # shown as info (it does not make the run fail).
        ui.heading("Wrong answers (read these when revising the generator)")
        for r in wrong:
            ui.info(ui.c("WRONG ", "red", "bold") + f"{r['model_label']} / {r['task_id']}: said "
                    f"{r['extracted_answer']!r}, expected {r['correct_answer']!r}")
            ui.detail(r["score_note"])

    n = len(tasks)
    counts = summary["counts"]
    if n and counts["both_pass"] / n >= 0.8:
        ui.warn(f"{counts['both_pass']}/{n} tasks were solved by BOTH models: the set may be too easy "
                "(rubric: task difficulty). Make tasks harder, then regenerate and re-evaluate.")
    if n and counts["both_fail"] / n >= 0.5:
        ui.warn(f"{counts['both_fail']}/{n} tasks were failed by BOTH models: check whether they are fair, "
                "unambiguous and verifiable before calling them 'hard'. Inspect these first.")
    ui.info(f"tasks: {counts['both_pass']} both-pass, {counts['split']} split, "
            f"{counts['both_fail']} both-fail, {counts['incomplete']} incomplete")
    return summary


def write_summary_files(cfg: Config, summary: dict, tasks: list[Task], model_labels: list[str]) -> list[str]:
    cfg.results_dir.mkdir(parents=True, exist_ok=True)
    (cfg.results_dir / "summary.json").write_text(
        json.dumps({"timestamp": dt.datetime.now().isoformat(timespec="seconds"), **summary}, indent=2),
        encoding="utf-8")
    lines = ["# Pilot / evaluation results", "",
             f"_Generated {dt.datetime.now().isoformat(timespec='seconds')} by `python run_tests.py`. "
             "Paste or adapt this into the README's 'Pilot results' section._", "",
             "| model | correct / valid | accuracy | invalid | cost (USD) |", "|---|---|---|---|---|"]
    for label, m in summary["per_model"].items():
        acc = "n/a" if m["accuracy"] is None else f"{m['accuracy']:.0%}"
        lines.append(f"| {label} | {m['correct']}/{m['valid']} | {acc} | {m['invalid']} | {m['cost_usd']:.4f} |")
    lines += ["", "| task | " + " | ".join(model_labels) + " | verdict |",
              "|---|" + "---|" * (len(model_labels) + 1)]
    for t in tasks:
        info = summary["tasks"][t.id]
        lines.append(f"| {t.id} | " + " | ".join(info["cells"][l] for l in model_labels) + f" | {info['verdict']} |")
    (cfg.results_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ["results/summary.json", "results/summary.md"]


def build_report(responses_path=None, out_path=None) -> None:
    """Original one-function API: per-model accuracy from results/responses.csv, printed (and written to out_path)."""
    import csv
    from collections import defaultdict
    from pathlib import Path

    path = Path(responses_path or RESPONSES_PATH)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run the evaluation and scoring stages first")
    counts: dict = defaultdict(lambda: [0, 0])
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("status") != "ok":
                continue
            counts[row.get("model", "?")][1] += 1
            counts[row.get("model", "?")][0] += str(row.get("correct", "")).lower() in ("true", "1", "yes")
    lines = [f"{model}: {c}/{n} correct" + (f" ({c / n:.0%})" if n else "") for model, (c, n) in counts.items()]
    text = "\n".join(lines) or "no valid responses found"
    print(text)
    if out_path:
        Path(out_path).write_text(text + "\n", encoding="utf-8")
