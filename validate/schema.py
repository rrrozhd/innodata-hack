"""Task-record checks that mirror the organizers' format rules for tasks.jsonl."""
from __future__ import annotations

ANSWER_TYPES = ("number", "integer", "label", "multiple_choice", "sequence")
REQUIRED = ("task_id", "image", "question", "answer", "answer_type")
EXTRA = ("question_type", "seed", "generator_version", "data")


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def record(rec: dict) -> tuple:
    for k in REQUIRED + EXTRA:
        if rec.get(k) in (None, "", []):
            return False, f"missing field {k!r}"
    if not isinstance(rec["task_id"], str) or not isinstance(rec["question"], str):
        return False, "task_id and question must be strings"
    img = rec["image"].replace("\\", "/")
    if img.startswith("/") or ":" in img[:3]:
        return False, "image path must be relative to the task folder"
    at, ans = rec["answer_type"], rec["answer"]
    has_rel, has_abs = rec.get("tolerance_rel") is not None, rec.get("tolerance_abs") is not None
    if at not in ANSWER_TYPES:
        return False, f"answer_type {at!r} is not allowed"
    if at == "integer" and not (_is_number(ans) and float(ans).is_integer()):
        return False, "integer answer is not a whole number"
    if at == "label" and not isinstance(ans, str):
        return False, "label answer is not a string"
    if at == "sequence" and not (isinstance(ans, list) and ans):
        return False, "sequence answer is not a non-empty list"
    if at == "number":
        if not _is_number(ans):
            return False, "number answer is not a JSON number"
        if has_rel == has_abs:
            return False, "a number answer needs exactly one of tolerance_rel and tolerance_abs"
        tol = rec["tolerance_rel"] if has_rel else rec["tolerance_abs"]
        if not _is_number(tol) or tol <= 0 or (has_rel and tol >= 1):
            return False, "tolerance is not a positive number (tolerance_rel is a fraction)"
        stated = {f"{tol:g}", repr(tol)}
        if has_rel:
            stated |= {f"{tol * 100:g}%", f"{tol * 100:g} %", f"{tol * 100:g} percent"}
        if not any(s in rec["question"] for s in stated):
            return False, "the tolerance is not stated in the question"
    elif has_rel or has_abs:
        return False, f"a tolerance is set on a {at} answer"
    return True, "task record has the required fields in the required form"
