"""Hand check: a person answers each task before the stored answer is shown.

    python -m eval.handcheck --tasks tasks/final

The answer is scored with the same scorer the models get. Results go to
handcheck.json in the task folder, and a README section is printed to paste in.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from factory.build import load_task_set

from .score import score


def _open(path: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # noqa: S606 - opens the image in the default viewer
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:  # noqa: BLE001 - no viewer available: the path is printed anyway
        pass


def readme_section(results: dict) -> str:
    lines = ["## Hand check", "",
             "Each task was answered by a team member from the image and question alone, before the stored "
             "answer was shown.", "", "| Task | Human answer | Matches reference | Time | Note |", "|---|---|---|---|---|"]
    for tid, r in results.items():
        lines.append(f"| {tid} | {r['human_answer']} | {'yes' if r['agrees'] else 'NO'} | {r['seconds']} s | "
                     f"{r['note']} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--who", default="", help="initials of the person checking")
    a = ap.parse_args(argv)
    folder = Path(a.tasks)
    out_file = folder / "handcheck.json"
    results = json.loads(out_file.read_text(encoding="utf-8")) if out_file.exists() else {}
    for rec, _ in load_task_set(folder):
        tid = rec["task_id"]
        if tid in results:
            continue
        image = folder / rec["image"]
        print("\n" + "=" * 100 + f"\n{tid}\nimage: {image}\n")
        print(rec["question"] + "\n")
        _open(image)
        started = time.time()
        human = input("Your answer (blank to stop): ").strip()
        if not human:
            break
        seconds = round(time.time() - started)
        ok, _ = score(human, rec)
        print(f"stored answer: {rec['answer']}  ->  {'MATCH' if ok else 'DIFFERENT'}")
        note = input("Short note (was anything unclear or hard to read?): ").strip()
        results[tid] = {"human_answer": human, "agrees": bool(ok), "seconds": seconds, "note": note, "who": a.who}
        out_file.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                            newline="\n")
    print("\n" + readme_section(results))
    disagree = [t for t, r in results.items() if not r["agrees"]]
    if disagree:
        print("Tasks where the human answer differs; inspect before counting them as valid: " + ", ".join(disagree))
    return 0


if __name__ == "__main__":
    sys.exit(main())
