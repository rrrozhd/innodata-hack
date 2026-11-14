"""Human review sheet: every task's picture, question, correct answer and how to check it by hand.

Writes two files next to the tasks (no new folders):
  tasks/review.html   open it in a browser; each task has the picture and the question, the correct answer is HIDDEN until
                      you click "show answer", so a person can try the task first and then check themselves.
  tasks/REVIEW.md     the same in plain text (everything visible), with the picture linked.
If results/responses.csv exists (stage 4/5 has run) each task also lists what each model answered and whether it was marked correct.

    python3 -m generate.review_sheet           (run from anywhere; it is also run automatically after generate and after scoring)
It only READS tasks/tasks.jsonl, tasks/images/ and results/responses.csv. Nothing here changes any task.
"""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path

HOW_TO = {
    "ecg_beat_count": [
        "Count every tall, narrow QRS spike (the sharp peak of each heartbeat). Ignore the small rounded bumps (P and T waves) and the noise.",
        "Mark each peak with a pencil on a printout, or count in blocks of 5 large boxes (1 second = 5 large boxes).",
    ],
    "ecg_rate": [
        "Find the spacing between two consecutive tall peaks in large boxes (the darker 5 mm squares). 1 large box = 0.2 s.",
        "Rate in beats per minute = 300 / (large boxes between peaks). Check a few neighbouring pairs; the strip is steady.",
    ],
    "ecg_premature_beat": [
        "Find the one beat that is wider and taller than the rest, arrives early (a shorter gap before it) and has no small bump before it.",
        "Count the tall peaks from the left, first peak = 1, and report the position of that early beat.",
    ],
    "ecg_interval": [
        "Count beats from the left (first peak = 1) to find the two named beats.",
        "Count the large boxes (darker 5 mm squares, 0.2 s each) from the tip of the first peak to the tip of the second; count a small box as 0.2 of a large box.",
    ],
    "ecg_ectopic_count": [
        "Find every beat that is wider and taller than the normal beats, arrives early and has no small bump before it.",
        "Count them. Two of them are never next to each other, and the normal beats between them are narrow and sharp.",
    ],
}
DEFAULT_HOW_TO = ["Read the picture carefully against the question and compare with the answer shown."]
NOTE = ("These are synthetic measuring-and-counting tasks made from a seeded generator. They are not clinical cases and "
        "nothing here is a diagnosis or medical advice.")


def _load_tasks(root: Path) -> list[dict]:
    path = root / "tasks" / "tasks.jsonl"
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if not rec.get("_placeholder"):
                    out.append(rec)
    return out


def _load_answers(root: Path) -> dict[str, list[dict]]:
    path = root / "results" / "responses.csv"
    by_task: dict[str, list[dict]] = {}
    if path.exists():
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                by_task.setdefault(row["task_id"], []).append(row)
    return by_task


def _accept(rec: dict) -> str:
    tol = rec.get("tolerance")
    if rec.get("answer_type") == "number":
        return f"{rec['reference_answer']}" + (f" (accepted: within +/-{tol:g})" if tol else " (exact)")
    return f"{rec['reference_answer']}"


def build(root: Path) -> tuple[Path, Path] | None:
    """Write tasks/review.html and tasks/REVIEW.md. Returns their paths, or None when there are no tasks."""
    tasks = _load_tasks(root)
    if not tasks:
        return None
    answers = _load_answers(root)
    md = ["# Review sheet", "", NOTE, "",
          "Open `tasks/review.html` in a browser to try each task before revealing the answer. "
          "Check a task by hand with `python3 -m validate.hand_check TASK_ID --by \"Your Name\"`.", ""]
    cards = []
    for rec in tasks:
        tid, img = rec["task_id"], rec["image_path"]
        how = HOW_TO.get(rec.get("generator", ""), DEFAULT_HOW_TO)
        # only show replies that were scored against THIS task's reference answer (a re-generated task with the same id is not the same task)
        model_rows = [r for r in answers.get(tid, []) if str(r.get("reference_answer", rec["reference_answer"])) == str(rec["reference_answer"])]
        md += [f"## {tid}", "", f"![{tid}](images/{Path(img).name})", "", f"**Question:** {rec['question']}", "",
               f"**Correct answer:** {_accept(rec)}", "", "**How to check it by hand:**"] + [f"- {h}" for h in how]
        md += ["", f"Data it was drawn from: `{rec.get('data', '')}` (seed {rec.get('seed')}).", ""]
        rows_html = ""
        if model_rows:
            md += ["| model | answered | marked |", "|---|---|---|"]
            for r in model_rows:
                mark = "correct" if r.get("correct") == "true" else ("wrong" if r.get("correct") == "false" else r.get("status", ""))
                md.append(f"| {r['model']} | {r['extracted_answer']} | {mark} |")
                rows_html += (f"<tr><td>{html.escape(r['model'])}</td><td>{html.escape(r['extracted_answer'])}</td>"
                              f"<td class='{'ok' if mark == 'correct' else 'bad'}'>{html.escape(mark)}</td></tr>")
            md.append("")
            rows_html = f"<table><tr><th>model</th><th>answered</th><th>marked</th></tr>{rows_html}</table>"
        how_html = "".join(f"<li>{html.escape(h)}</li>" for h in how)
        cards.append(
            f"<section><h2>{html.escape(tid)}</h2>"
            f"<a href='images/{html.escape(Path(img).name)}'><img src='images/{html.escape(Path(img).name)}' alt='{html.escape(tid)}'></a>"
            f"<p class='q'><b>Question:</b> {html.escape(rec['question'])}</p>"
            f"<details><summary>Show correct answer and how to check</summary>"
            f"<p class='a'><b>Correct answer:</b> {html.escape(_accept(rec))}</p><ul>{how_html}</ul>"
            f"<p class='m'>Drawn from <code>{html.escape(str(rec.get('data', '')))}</code> (seed {rec.get('seed')}).</p>{rows_html}</details></section>")
    page = ("<!doctype html><meta charset='utf-8'><title>Review sheet</title><style>"
            "body{font:16px/1.45 system-ui,sans-serif;max-width:1280px;margin:24px auto;padding:0 16px;color:#222}"
            "section{border:1px solid #ddd;border-radius:8px;padding:12px 16px;margin:18px 0}"
            "img{max-width:100%;border:1px solid #ccc}.q{font-size:17px}.a{font-size:17px;color:#064}"
            "details summary{cursor:pointer;color:#06c}table{border-collapse:collapse;margin-top:8px}"
            "td,th{border:1px solid #ccc;padding:4px 10px}.ok{color:#064}.bad{color:#b00}.m{color:#666;font-size:14px}"
            "</style><h1>Review sheet</h1><p>" + html.escape(NOTE) + "</p>"
            "<p>Look at each picture, work out your own answer, then open &ldquo;Show correct answer&rdquo;.</p>" + "".join(cards))
    out_html, out_md = root / "tasks" / "review.html", root / "tasks" / "REVIEW.md"
    out_html.write_text(page, encoding="utf-8")
    out_md.write_text("\n".join(md) + "\n", encoding="utf-8")
    return out_html, out_md


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    made = build(root)
    print("no tasks to review (run the generators first)" if not made else "wrote tasks/review.html and tasks/REVIEW.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
