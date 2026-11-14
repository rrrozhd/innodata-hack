"""Task record schema (tasks/tasks.jsonl).

The repo's tasks.jsonl record is: task_id, image_path, question, data (path to the data JSON),
reference_answer, checks. The shell works on the internal `Task` object (id, data_path, answer, ...) and
translates at the edges with `to_official` / `from_official` (bottom of this file). The generator's extra
fields (generator, seed, visual_type, question_type, answer_type, tolerance, real_world_examples, ...) are
written as additional top-level keys. A record with `"_placeholder": true` is skipped when loading.
PROVISIONAL: confirm the official schema with the organizers; if it differs, edit ONLY the adapter.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import MISSING, asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.1-provisional"
ANSWER_TYPES = ("number", "text", "choice")


@dataclass
class Task:
    id: str
    generator: str
    index: int
    seed: int
    visual_type: str
    question_type: str
    image_path: str  # relative to the project root
    data_path: str  # relative to the project root
    question: str  # the FULL prompt text (tolerance already stated)
    answer: Any
    answer_type: str  # number | text | choice
    tolerance: float | None
    real_world_examples: list[str]
    notes: str = ""
    schema_version: str = SCHEMA_VERSION
    extra: dict = field(default_factory=dict)


_FIELD_NAMES = [f.name for f in fields(Task)]
_REQUIRED = [
    f.name for f in fields(Task)
    if f.default is MISSING and f.default_factory is MISSING
]


OFFICIAL_NAME = {"id": "task_id", "data_path": "data", "answer": "reference_answer"}


def _official(name: str) -> str:
    return OFFICIAL_NAME.get(name, name)


def _validate_record(rec: dict) -> list[str]:
    problems = []
    missing = [_official(name) for name in _REQUIRED if name not in rec]
    if missing:
        problems.append("missing field(s): " + ", ".join(missing))
        return problems
    for name in ("id", "generator", "visual_type", "question_type", "image_path", "data_path", "question"):
        if not isinstance(rec[name], str) or not rec[name].strip():
            problems.append(f"'{_official(name)}' must be a non-empty string")
    for name in ("index", "seed"):
        if not isinstance(rec[name], int) or isinstance(rec[name], bool):
            problems.append(f"'{name}' must be an integer")
    if rec["answer_type"] not in ANSWER_TYPES:
        problems.append(f"'answer_type' must be one of {ANSWER_TYPES}, got {rec['answer_type']!r}")
    tol = rec["tolerance"]
    if tol is not None and (not isinstance(tol, (int, float)) or isinstance(tol, bool) or tol < 0):
        problems.append("'tolerance' must be null or a non-negative number")
    ex = rec["real_world_examples"]
    if not isinstance(ex, list) or not all(isinstance(e, str) for e in ex):
        problems.append("'real_world_examples' must be a list of strings")
    return problems


def load_tasks(path: Path) -> tuple[list[Task], list[str]]:
    """Read tasks.jsonl. Returns (tasks, problems). Never raises on bad lines; reports them."""
    tasks: list[Task] = []
    problems: list[str] = []
    if not path.exists():
        return [], [f"{path} does not exist"]
    seen: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"line {lineno}: invalid JSON ({exc})")
            continue
        if not isinstance(raw, dict):
            problems.append(f"line {lineno}: each line must be a JSON object")
            continue
        if raw.get("_placeholder"):
            continue
        rec = from_official(raw)
        issues = _validate_record(rec)
        if issues:
            problems.append(f"line {lineno} (task_id={raw.get('task_id', rec.get('id', '?'))}): " + "; ".join(issues))
            continue
        if rec["id"] in seen:
            problems.append(f"line {lineno}: duplicate task_id {rec['id']!r}")
            continue
        seen.add(rec["id"])
        known = {k: v for k, v in rec.items() if k in _FIELD_NAMES}
        unknown = {k: v for k, v in rec.items() if k not in _FIELD_NAMES}
        if unknown:
            known["extra"] = {**known.get("extra", {}), **unknown}
        tasks.append(Task(**known))
    return tasks, problems


def save_tasks(path: Path, tasks: list[Task]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for task in tasks:
            fh.write(json.dumps(to_official(task), ensure_ascii=False, sort_keys=True) + "\n")


def task_fingerprint(root: Path, task: Task) -> str:
    """Hash of everything a human hand-checks: question, answer, data file, image file."""
    h = hashlib.sha256()
    h.update(task.question.encode("utf-8"))
    h.update(json.dumps(task.answer, sort_keys=True).encode("utf-8"))
    for rel in (task.data_path, task.image_path):
        p = root / rel
        h.update(p.read_bytes() if p.exists() else b"<missing>")
    return h.hexdigest()[:16]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---- SCHEMA ADAPTER: edit these two functions to match the official format --------------------
DEFAULT_CHECKS = {
    "render": "generator verify_render hook (validate/check_render.py)",
    "reference": "answer recomputed from the saved data (validate/check_answer.py)",
    "needs_image": "question leak check (validate/check_needs_image.py)",
}


def to_official(task: Task) -> dict:
    """Internal Task -> the dict written to tasks/tasks.jsonl (the repo's field names)."""
    rec = asdict(task)
    extra = rec.pop("extra", {}) or {}
    rec["task_id"] = rec.pop("id")
    rec["data"] = rec.pop("data_path")
    rec["reference_answer"] = rec.pop("answer")
    rec["checks"] = extra.pop("checks", None) or dict(DEFAULT_CHECKS)
    rec.update(extra)
    return rec


def from_official(rec: dict) -> dict:
    """A tasks.jsonl dict -> the dict of internal field names."""
    out = dict(rec)
    if "task_id" in out:
        out["id"] = out.pop("task_id")
    if "reference_answer" in out:
        out["answer"] = out.pop("reference_answer")
    if isinstance(out.get("data"), str):
        out["data_path"] = out.pop("data")
    elif "data" in out:
        out.pop("data")  # inline data is not supported: the data JSON file is the source of truth
    return out
