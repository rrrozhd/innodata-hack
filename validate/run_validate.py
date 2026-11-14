"""Validate tasks: one function per task, and a CLI over a task folder.

    python -m validate.run_validate --tasks tasks/final

A task counts only if every check passes. Each check records what it establishes
and where its limit is; nothing here claims more than that.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from factory.core import registry
from factory.core.canvas import load_rgb

from . import image_dependence, legibility, ocr, question_parse, render_checks, schema, tolerance, uniqueness
from .families import FAMILIES

VALIDATOR_VERSION = "0.2.0"

LIMITS = {
    "schema": "Format only.",
    "hashes": "Detects a changed file, not a wrong one.",
    "question_matches_data": "Shows the stored question is the one the generator words for this scene.",
    "rerender": "Same renderer, same machine: shows the PNG is a pure function of the data, not that the "
                "renderer draws correctly.",
    "read_image": "Finds the train colours in the PNG; uses the plot rectangle, station heights, time span and "
                  "colours recorded by the renderer as calibration.",
    "timetable_matches_data": "Every train path from the data is traced in the PNG in its colour, its number "
                              "sits at its start, and no train pixel is unexplained; does not rebuild the timetable from pixels alone.",
    "independent_answer": "Second solver (time sampling, not crossing algebra) driven by the question text; works "
                          "from the stored data, which the pixel check ties to the image.",
    "answer_not_in_question": "String match only.",
    "fingerprints": "Checks the stored table is self-consistent, not that the list of shortcuts is complete.",
    "labels_legible": "Assumes a worst-case downscale of 0.667x; real provider resize policies are unknown.",
    "ocr_labels": "Runs only where Tesseract is installed (the Docker image); samples labels on dense drawings.",
    "image_dependence": "Shows the question text alone does not determine the answer; guessing rates are "
                        "measured separately by the blind baseline.",
    "tolerance": "No task in this family has a continuous numeric answer.",
}


def _same(got, ref, record) -> bool:
    tol = record.get("tolerance_abs")
    if tol is not None and isinstance(got, (int, float)) and isinstance(ref, (int, float)):
        return abs(got - ref) <= 1e-6   # the independent value must equal the reference, not merely be near it
    return got == ref


def validate_task(record: dict, data: dict, png: bytes) -> dict:
    checks = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail, "limit": LIMITS.get(name, "")})

    def run(name, fn, *args):
        try:
            ok, detail = fn(*args)
        except Exception as e:  # noqa: BLE001 - a crashing check is a failed check
            ok, detail = False, f"{type(e).__name__}: {e}"
        add(name, "pass" if ok else "fail", detail)
        return ok

    run("schema", schema.record, record)
    run("hashes", render_checks.hashes, record, png)
    scene, layout = data["scene"], data["layout"]
    family = FAMILIES[scene["family"]]
    program = scene["program"]
    prog = registry.program(program)
    same_q = prog.question(scene) == record.get("question")
    add("question_matches_data", "pass" if same_q else "fail",
        "the question is exactly what the generator words for this scene" if same_q
        else "the stored question differs from the one generated from the data")
    run("rerender", render_checks.rerender, data, png)

    img = load_rgb(png)
    sized = img.shape[0] == layout["height"] and img.shape[1] == layout["width"]
    state = parsed = None
    try:
        if not sized:
            raise ValueError(f"image is {img.shape[1]}x{img.shape[0]}, layout says "
                             f"{layout['width']}x{layout['height']}")
        state = family.read(img, layout)
        add("read_image", "pass", family.describe(state))
    except Exception as e:  # noqa: BLE001
        add("read_image", "fail", f"{type(e).__name__}: {e}")

    if state is not None:
        try:
            for name, ok, detail in family.data_checks(data, state):
                add(name, "pass" if ok else "fail", detail)
        except Exception as e:  # noqa: BLE001
            add("data_checks", "fail", f"{type(e).__name__}: {e}")
        try:
            parsed = question_parse.PARSERS[program](record["question"])
            got = family.answer(state, program, parsed, data)
            ok = _same(got, record["answer"], record)
            add("independent_answer", "pass" if ok else "fail",
                f"solving from the PNG gives {got}; the reference is {record['answer']}")
        except Exception as e:  # noqa: BLE001
            parsed = None
            add("independent_answer", "fail", f"{type(e).__name__}: {e}")
    else:
        add("independent_answer", "fail", "not run: the image could not be read")

    run("answer_not_in_question", uniqueness.not_in_question, record)
    run("fingerprints", uniqueness.fingerprints, record, data)
    if sized:
        run("labels_legible", legibility.labels, img, layout)
    else:
        add("labels_legible", "fail", "not run: image size does not match the layout")

    needed = set()
    if parsed:
        for v in parsed.values():
            if isinstance(v, str):
                needed.add(v)
            elif isinstance(v, list):
                needed |= {x for x in v if isinstance(x, str)}
    try:
        status, detail = ocr.read_labels(png, layout, needed)
    except Exception as e:  # noqa: BLE001
        status, detail = "fail", f"{type(e).__name__}: {e}"
    add("ocr_labels", status, detail)

    if state is not None and parsed is not None:
        run("image_dependence", image_dependence.twin, record, data, family, parsed)
    else:
        add("image_dependence", "fail", "not run: the image could not be solved")
    run("tolerance", tolerance.check, record, data)

    return {"task_id": record.get("task_id"), "validator_version": VALIDATOR_VERSION,
            "passed": all(c["status"] in ("pass", "skipped") for c in checks),
            "skipped": [c["name"] for c in checks if c["status"] == "skipped"], "checks": checks}


def failed(report: dict) -> list:
    return [c["name"] for c in report["checks"] if c["status"] == "fail"]


def validate_folder(folder: Path, write: bool = True) -> list:
    reports = []
    for line in (folder / "tasks.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        data = json.loads((folder / rec["data"]).read_text(encoding="utf-8"))
        png = (folder / rec["image"]).read_bytes()
        rep = validate_task(rec, data, png)
        reports.append(rep)
        if write:
            out = folder / "checks" / f"{rec['task_id']}.checks.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return reports


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validate every task in a task folder.")
    ap.add_argument("--tasks", required=True, help="task folder holding tasks.jsonl")
    ap.add_argument("--no-write", action="store_true", help="do not rewrite the checks/ files")
    a = ap.parse_args(argv)
    reports = validate_folder(Path(a.tasks), write=not a.no_write)
    bad = [r for r in reports if not r["passed"]]
    for r in reports:
        skipped = f" (skipped: {', '.join(r['skipped'])})" if r["skipped"] else ""
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['task_id']}{skipped}"
              + ("" if r["passed"] else "  <- " + ", ".join(failed(r))))
    print(f"\n{len(reports) - len(bad)} of {len(reports)} tasks valid.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
