"""Test the validators by corrupting valid tasks and confirming each corruption
is rejected.

    python -m validate.selftest --tasks tasks/final

Each corruption changes the image, the data or the record the way a pipeline bug
could. The recorded hashes are refreshed after every corruption, so the simple
hash check cannot be what catches it: a deeper check has to.
Limit: only the corruptions listed here are covered.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from factory.core.canvas import file_sha256, load_rgb, pixel_sha256

from .run_validate import failed, validate_task


def _png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8), "RGB").save(buf, format="PNG")
    return buf.getvalue()


def _rehash(record: dict, png: bytes) -> dict:
    record = dict(record)
    record["image_sha256"], record["pixel_sha256"] = file_sha256(png), pixel_sha256(png)
    return record


def _long_link(data: dict) -> dict:
    """A link with a sideways run long enough to cut a piece out of."""
    return max(data["scene"]["links"], key=lambda l: abs(l["route"][0] - l["route"][3]))


def erase_link(record, data, png):
    """Rub out a stretch of one ownership line in the image."""
    xp, _, yt, xc, _ = _long_link(data)["route"]
    if abs(xp - xc) < 40:
        return None
    mid = (xp + xc) // 2
    img = load_rgb(png).copy()
    img[yt - 2:yt + 2, mid - 8:mid + 8] = 255
    out = _png(img)
    return _rehash(record, out), data, out


def add_link(record, data, png):
    """Draw the start of an ownership line that is not in the data."""
    boxes, rows = data["layout"]["boxes"], data["layout"]["rows"]
    top = next(b for b in boxes if b["id"] == rows[0][0])
    x = top["x0"] + 6
    if any(abs(l["route"][0] - x) < 9 for l in data["scene"]["links"] if l["owner"] == top["id"]):
        return None
    img = load_rgb(png).copy()
    img[top["y1"]:top["y1"] + 9, x - 1:x + 1] = 0     # a stub leaving the first box and ending in mid air
    out = _png(img)
    return _rehash(record, out), data, out


def change_percentage(record, data, png):
    """Change one percentage in the stored data without redrawing the image."""
    data = copy.deepcopy(data)
    link = _long_link(data)
    link["pct"] = "99" if link["pct"] != "99" else "98"
    return record, data, png


def redirect_link(record, data, png):
    """Point one stored link at a different entity without redrawing the image."""
    data = copy.deepcopy(data)
    link = _long_link(data)
    row = next(r for r in data["scene"]["rows"] if link["owned"] in r)
    others = [n for n in row if n != link["owned"]]
    if not others:
        return None
    link["owned"] = others[0]
    return record, data, png


def erase_label(record, data, png):
    """Blank out the first line of one entity's name."""
    lab = next(l for l in data["layout"]["labels"] if l["role"] == "entity")
    img = load_rgb(png).copy()
    x0, y0, x1, y1 = (int(v) for v in lab["bbox"])
    img[y0 - 1:y1 + 2, x0 - 1:x1 + 2] = lab["bg"]
    out = _png(img)
    return _rehash(record, out), data, out


def edit_question(record, data, png):
    """Change a rule stated in the question."""
    a, b = data["scene"]["selftest"]["question_swap"]
    if a not in record["question"]:
        return None
    return dict(record, question=record["question"].replace(a, b)), data, png


def wrong_answer(record, data, png):
    """Store a different reference answer."""
    alt = next((f["answer"] for f in data["scene"]["fingerprints"] if f["armed"]), None)
    if alt is None:
        return None
    return dict(record, answer=alt), data, png


def flip_fingerprint(record, data, png):
    """Mark an armed trap as disarmed."""
    data = copy.deepcopy(data)
    hit = next((f for f in data["scene"]["fingerprints"] if f["armed"]), None)
    if hit is None:
        return None
    hit["armed"] = False
    return record, data, png


def crop_image(record, data, png):
    """Cut off the right part of the image."""
    out = _png(load_rgb(png)[:, :-220])
    return _rehash(record, out), data, out


def leak_answer(record, data, png):
    """Append the answer to the question text."""
    ans = record["answer"]
    text = ", ".join(ans) if isinstance(ans, list) else str(ans)
    return dict(record, question=record["question"] + f"\n(Hint: the answer is {text} exactly.)"), data, png


CORRUPTIONS = [erase_link, add_link, change_percentage, redirect_link, erase_label, edit_question, wrong_answer,
               flip_fingerprint, crop_image, leak_answer]


def run_one(record: dict, data: dict, png: bytes) -> list:
    """Return [{"corruption", "rejected", "by"}] for one valid task."""
    rows = []
    for fn in CORRUPTIONS:
        made = fn(copy.deepcopy(record), data, png)
        if made is None:
            rows.append({"corruption": fn.__name__, "rejected": None, "by": ["not applicable to this task"]})
            continue
        report = validate_task(*made)
        deeper = [n for n in failed(report) if n != "hashes"]
        rows.append({"corruption": fn.__name__, "rejected": bool(deeper), "by": deeper})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Corrupt valid tasks and confirm the validators reject them.")
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--limit", type=int, default=5, help="how many tasks to corrupt")
    a = ap.parse_args(argv)
    folder = Path(a.tasks)
    survived = 0
    lines = [l for l in (folder / "tasks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for line in lines[:a.limit]:
        rec = json.loads(line)
        data = json.loads((folder / rec["data"]).read_text(encoding="utf-8"))
        png = (folder / rec["image"]).read_bytes()
        print(rec["task_id"])
        for row in run_one(rec, data, png):
            state = {True: "rejected", False: "SURVIVED", None: "n/a"}[row["rejected"]]
            survived += row["rejected"] is False
            print(f"  {state:9s} {row['corruption']:18s} {', '.join(row['by'][:4])}")
    print(f"\n{survived} corruption(s) survived.")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
