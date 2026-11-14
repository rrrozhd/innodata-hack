"""Select the final showcase from an evaluated task set, by a fixed rule.

    python -m eval.curate --tasks tasks/fresh_v1 --results results/fresh_v1 --out tasks/final

Rule, in order:
1. valid tasks only (and hand-checked ones only, with --require-handcheck);
2. tasks both models got wrong, then tasks one model got wrong, then the rest by
   highest difficulty level;
3. at most two tasks per question type; within a tier prefer tasks whose wrong
   answers matched a fingerprint, then lower task_id for a stable order.

The command writes a manifest of seeds. `factory reproduce` rebuilds those tasks
and runs the clean final evaluation, whose results are reported unchanged.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from factory import GENERATOR_VERSION
from factory.build import load_task_set

MAX_PER_TYPE = 2


def select(tasks: list, rows: list, n: int, handchecked: set | None = None) -> tuple:
    by_task = defaultdict(list)
    for r in rows:
        by_task[r["task_id"]].append(r)
    n_models = len({r["model"] for r in rows})
    pool = []
    for rec, _ in tasks:
        mine = by_task.get(rec["task_id"], [])
        if not rec.get("checks_passed") or len(mine) != n_models or any(r["correct"] not in ("0", "1") for r in mine):
            continue
        if handchecked is not None and rec["task_id"] not in handchecked:
            continue
        wrong = sum(1 for r in mine if r["correct"] == "0")
        matched = sum(1 for r in mine if r["correct"] == "0"
                      and r.get("fingerprint") not in ("", "unmatched", "no_answer", None))
        tier = 0 if wrong == n_models else (1 if wrong else 2)
        pool.append((tier, -matched, -rec["difficulty"]["level"], rec["task_id"], rec, wrong))
    pool.sort(key=lambda x: x[:4])
    chosen, per_type = [], defaultdict(int)
    for tier, _, _, _, rec, wrong in pool:
        if len(chosen) == n:
            break
        if per_type[rec["question_type"]] >= MAX_PER_TYPE:
            continue
        per_type[rec["question_type"]] += 1
        chosen.append((rec, tier, wrong))
    if len(chosen) < n:  # not enough question types yet to fill n at two per type: relax the cap, same order
        picked = {c[0]["task_id"] for c in chosen}
        for tier, _, _, _, rec, wrong in pool:
            if len(chosen) == n:
                break
            if rec["task_id"] not in picked:
                chosen.append((rec, tier, wrong))
    mix = {"both_models_wrong": sum(1 for c in chosen if c[1] == 0),
           "one_model_wrong": sum(1 for c in chosen if c[1] == 1),
           "no_model_wrong": sum(1 for c in chosen if c[1] == 2)}
    return chosen, {"pool_size": len(tasks), "eligible": len(pool), "selected": len(chosen), "mix": mix,
                    "pool_both_wrong": sum(1 for p in pool if p[0] == 0)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True, help="folder that will hold the final manifest")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--require-handcheck", action="store_true")
    a = ap.parse_args(argv)
    tasks = load_task_set(Path(a.tasks))
    with open(Path(a.results) / "responses.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    handchecked = None
    if a.require_handcheck:
        hc = Path(a.tasks) / "handcheck.json"
        if not hc.exists():
            print("no handcheck.json in the task folder; run `factory handcheck` first")
            return 1
        handchecked = {k for k, v in json.loads(hc.read_text(encoding="utf-8")).items() if v.get("agrees")}
    chosen, stats = select(tasks, rows, a.n, handchecked)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"generator_version": GENERATOR_VERSION, "selected_from": str(a.tasks).replace("\\", "/"),
                "selection": stats,
                "tasks": [{"program": rec["question_type"], "seed": rec["seed"],
                           "level": rec["difficulty"]["level"], "variant": rec["difficulty"]["variant"]}
                          for rec, _, _ in chosen]}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8",
                                       newline="\n")
    print(json.dumps(stats, indent=1))
    for rec, tier, wrong in chosen:
        print(f"  {rec['task_id']:40s} wrong for {wrong} model(s)")
    if stats["selected"] < a.n:
        print(f"only {stats['selected']} eligible tasks; extend the pool once, then report the mix as it is")
    print(f"manifest written to {out / 'manifest.json'}; now run: python -m factory reproduce --tasks {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
