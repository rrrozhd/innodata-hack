"""Seed -> task: build a scene, render it, compute the answer, validate, write.

A seed that fails a check moves to the next attempt in a fixed chain, so the
same seed always ends at the same task.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import GENERATOR_VERSION
from .core import registry
from .core.canvas import file_sha256, pixel_sha256
from .core.fingerprints import chance_match_rate
from .core.seed import Reject

MAX_ATTEMPTS = 40
PREFIX = {"traingraph": "tg"}


class BuildError(Exception):
    pass


def task_id(family: str, program: str, level: int, seed: int, variant: str = "") -> str:
    return f"{PREFIX[family]}-{program}-L{level}-s{seed}" + (f"-{variant}" if variant else "")


def _dump(obj) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True) + "\n"


def build_task(program: str, seed: int, level: int, variant: str = "", validate: bool = True) -> dict:
    """Return {"record", "data", "png", "report", "rejected"} for one task."""
    from validate.run_validate import failed, validate_task  # validation code lives in validate/

    prog = registry.program(program)
    rejected = []
    for attempt in range(MAX_ATTEMPTS):
        try:
            scene = prog.build(seed, level, attempt, variant)
        except Reject as e:
            rejected.append({"attempt": attempt, "stage": "generate", "reason": str(e)})
            continue
        png, layout = registry.render(scene)
        answer = prog.solve(scene)
        tid = task_id(prog.family, prog.name, level, seed, variant)
        fps = scene["fingerprints"]
        record = {
            "task_id": tid,
            "image": f"images/{tid}.png",
            "question": prog.question(scene),
            "answer": answer,
            "answer_type": prog.answer_type,
            "answer_format": prog.answer_format,
            "question_type": prog.name,
            "family": prog.family,
            "difficulty": {"level": level, "variant": variant,
                           "armed_traps": sorted(f["id"] for f in fps if f["armed"] and f["kind"] == "strategy")},
            "seed": seed,
            "attempt": attempt,
            "generator_version": GENERATOR_VERSION,
            "data": f"data/{tid}.json",
            "checks": f"checks/{tid}.checks.json",
            "image_sha256": file_sha256(png),
            "pixel_sha256": pixel_sha256(png),
            "references": registry.family_of(scene).REFERENCES,
        }
        for key in ("tolerance_abs", "tolerance_rel", "options"):
            if key in scene.get("record_extra", {}):
                record[key] = scene["record_extra"][key]
        data = {"task_id": tid, "generator_version": GENERATOR_VERSION, "seed": seed, "answer": answer,
                "answer_space": prog.answer_space,
                "chance_fingerprint_match": round(chance_match_rate(fps, prog.answer_space), 4),
                "scene": scene, "layout": layout}
        if not validate:
            record["checks_passed"] = None
            return {"record": record, "data": data, "png": png, "report": None, "rejected": rejected}
        report = validate_task(record, data, png)
        if report["passed"]:
            record["checks_passed"] = True
            report["rejected_attempts"] = rejected
            return {"record": record, "data": data, "png": png, "report": report, "rejected": rejected}
        rejected.append({"attempt": attempt, "stage": "validate", "reason": ", ".join(failed(report))})
    raise BuildError(f"{program} seed {seed} level {level}: no valid task in {MAX_ATTEMPTS} attempts; "
                     f"last reasons: {[r['reason'] for r in rejected[-3:]]}")


def write_task_set(folder: Path, tasks: list, spec: list | None = None) -> None:
    """Write tasks.jsonl, images, per-task data JSON, check reports and a manifest."""
    folder = Path(folder)
    for sub in ("images", "data", "checks"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    lines = []
    for t in tasks:
        rec = t["record"]
        (folder / rec["image"]).write_bytes(t["png"])
        (folder / rec["data"]).write_text(_dump(t["data"]), encoding="utf-8", newline="\n")
        if t["report"] is not None:
            (folder / rec["checks"]).write_text(json.dumps(t["report"], indent=1, ensure_ascii=False) + "\n",
                                                encoding="utf-8", newline="\n")
        lines.append(json.dumps(rec, ensure_ascii=False))
    (folder / "tasks.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    manifest = {"generator_version": GENERATOR_VERSION,
                "tasks": spec if spec is not None else [
                    {"program": t["record"]["question_type"], "seed": t["record"]["seed"],
                     "level": t["record"]["difficulty"]["level"], "variant": t["record"]["difficulty"]["variant"]}
                    for t in tasks]}
    (folder / "manifest.json").write_text(_dump(manifest), encoding="utf-8", newline="\n")


def load_task_set(folder: Path) -> list:
    """Return [(record, data)] for a task folder."""
    folder = Path(folder)
    out = []
    for line in (folder / "tasks.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            out.append((rec, json.loads((folder / rec["data"]).read_text(encoding="utf-8"))))
    return out
