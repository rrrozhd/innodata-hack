"""A single HTML page for looking through a task folder on your own machine."""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path

from .build import load_task_set

CSS = """
body{font:15px/1.5 system-ui,sans-serif;margin:24px;background:#f5f6f8;color:#1c222b}
.task{background:#fff;border:1px solid #d9dde3;border-radius:8px;padding:16px;margin:0 0 24px}
img{max-width:100%;border:1px solid #d9dde3}
pre{white-space:pre-wrap;background:#f5f6f8;padding:10px;border-radius:6px}
table{border-collapse:collapse;margin:8px 0}td,th{border:1px solid #d9dde3;padding:3px 8px;text-align:left}
.pass{color:#1a7f37}.fail{color:#c62828;font-weight:600}.skipped{color:#9a6700}
summary{cursor:pointer;font-weight:600}
"""


def write_gallery(tasks_dir: Path, results_dir: Path | None = None) -> Path:
    replies = {}
    if results_dir and (results_dir / "responses.csv").exists():
        with open(results_dir / "responses.csv", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                replies.setdefault(row["task_id"], []).append(row)
    parts = [f"<!doctype html><meta charset='utf-8'><title>{html.escape(tasks_dir.name)}</title>"
             f"<style>{CSS}</style><h1>{html.escape(str(tasks_dir))}</h1>"]
    for rec, data in load_task_set(tasks_dir):
        e = html.escape
        parts.append(f"<div class='task'><h2>{e(rec['task_id'])}</h2>"
                     f"<p>{e(rec['question_type'])} &middot; level {rec['difficulty']['level']} &middot; "
                     f"seed {rec['seed']} &middot; attempt {rec['attempt']}</p>"
                     f"<img src='{e(rec['image'])}' alt='task image'><pre>{e(rec['question'])}</pre>")
        parts.append(f"<details><summary>Reference answer</summary><p><b>{e(str(rec['answer']))}</b></p></details>")
        fps = data["scene"].get("fingerprints", [])
        rows = "".join(f"<tr><td>{e(f['id'])}</td><td>{e(f['kind'])}</td><td>{'armed' if f['armed'] else 'disarmed'}"
                       f"</td><td>{e(str(f['answer']))}</td><td>{e(f['description'])}</td></tr>" for f in fps)
        parts.append("<details><summary>Fingerprints (wrong answers the generator predicts)</summary><table>"
                     "<tr><th>id</th><th>kind</th><th>state</th><th>answer</th><th>what it means</th></tr>"
                     f"{rows}</table></details>")
        check_file = tasks_dir / rec.get("checks", "")
        if rec.get("checks") and check_file.exists():
            rep = json.loads(check_file.read_text(encoding="utf-8"))
            rows = "".join(f"<tr><td class='{c['status']}'>{c['status']}</td><td>{e(c['name'])}</td>"
                           f"<td>{e(c['detail'])}</td><td>{e(c['limit'])}</td></tr>" for c in rep["checks"])
            parts.append("<details><summary>Checks</summary><table><tr><th></th><th>check</th><th>result</th>"
                         f"<th>limit</th></tr>{rows}</table></details>")
        if rec["task_id"] in replies:
            rows = "".join(f"<tr><td>{e(r['model'])}</td><td>{e(r['parsed'])}</td>"
                           f"<td class='{'pass' if r['correct'] == '1' else 'fail'}'>{e(r['correct'])}</td>"
                           f"<td>{e(r.get('fingerprint', ''))}</td></tr>" for r in replies[rec["task_id"]])
            parts.append("<details open><summary>Model answers</summary><table><tr><th>model</th><th>answer</th>"
                         f"<th>correct</th><th>consistent with</th></tr>{rows}</table></details>")
        parts.append("</div>")
    out = tasks_dir / "gallery.html"
    out.write_text("\n".join(parts), encoding="utf-8", newline="\n")
    return out
