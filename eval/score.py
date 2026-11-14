"""Scoring: compare an extracted answer with the reference answer.

number : first number found in the reply (handles 1,234.5 / $12 / 45% / unicode minus), correct
         if |got - expected| <= tolerance (tolerance None/0 = exact, within float noise).
text   : equal after lowercasing and removing punctuation/extra spaces.
choice : equal letters (A-Z), ignoring case and surrounding punctuation like "(B)".
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?|[-+]?\.\d+")


def parse_number(text) -> float | None:
    if text is None:
        return None
    s = str(text).replace("−", "-").replace("–", "-")
    m = _NUMBER.search(s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def normalize_text(text) -> str:
    s = re.sub(r"[^\w\s]", " ", str(text).lower())
    return re.sub(r"\s+", " ", s).strip()


def parse_choice(text) -> str | None:
    m = re.search(r"\b([A-Za-z])\b", str(text))
    return m.group(1).upper() if m else None


def answers_equal(answer_type: str, expected, other) -> bool:
    """Strict equality used to check a stored answer against a recomputed one."""
    if answer_type == "number":
        a, b = parse_number(expected), parse_number(other)
        return a is not None and b is not None and abs(a - b) <= 1e-9 * max(1.0, abs(a))
    if answer_type == "choice":
        return parse_choice(expected) == parse_choice(other)
    return normalize_text(expected) == normalize_text(other)


def is_correct(answer_type: str, expected, got, tolerance) -> tuple[bool, str]:
    """Score one extracted answer. Returns (correct, note)."""
    if got is None:
        return False, "no 'FINAL ANSWER:' line found in the reply"
    if answer_type == "number":
        want, have = parse_number(expected), parse_number(got)
        if want is None:
            return False, f"the stored answer {expected!r} is not a number"
        if have is None:
            return False, f"could not read a number from {got!r}"
        tol = float(tolerance or 0)
        diff = abs(have - want)
        if diff <= tol + 1e-9 * max(1.0, abs(want)):
            return True, f"within tolerance (off by {diff:g}, allowed {tol:g})"
        return False, f"off by {diff:g}, allowed {tol:g}"
    if answer_type == "choice":
        have = parse_choice(got)
        ok = have is not None and have == parse_choice(expected)
        return ok, "" if ok else f"chose {have!r}, expected {parse_choice(expected)!r}"
    ok = normalize_text(got) == normalize_text(expected)
    return ok, "" if ok else f"{got!r} != {expected!r}"


# results/responses.csv: the repo's original columns come first (task_id, model, raw_path, extracted_answer,
# reference_answer, correct, timestamp), then the extra columns this runner adds.
CSV_COLUMNS = [
    "task_id", "model", "raw_path", "extracted_answer", "reference_answer", "correct", "timestamp",
    "model_id", "status", "score_note", "prompt_tokens", "completion_tokens", "reasoning_tokens",
    "cost_usd", "latency_s", "attempts",
]
_CSV_RENAME = {"model_label": "model", "correct_answer": "reference_answer"}


def build_rows(tasks, records) -> list[dict]:
    """One row per (task, model). Invalid responses are NOT scored (correct left blank)."""
    from eval.extract_answer import extract_final_answer

    by_id = {t.id: t for t in tasks}
    rows = []
    for rec in records:
        task = by_id[rec.task_id]
        extracted = extract_final_answer(rec.text) if rec.status == "ok" else None
        if rec.status == "ok":
            correct, note = is_correct(task.answer_type, task.answer, extracted, task.tolerance)
            correct_cell = str(correct).lower()
        else:
            correct_cell, note = "", f"not scored: response status is '{rec.status}'"
        rows.append({
            "task_id": rec.task_id, "model_label": rec.model_label, "model_id": rec.model_id,
            "status": rec.status, "extracted_answer": "" if extracted is None else extracted,
            "correct_answer": task.answer, "correct": correct_cell, "score_note": note,
            "prompt_tokens": rec.prompt_tokens, "completion_tokens": rec.completion_tokens,
            "reasoning_tokens": rec.reasoning_tokens,
            "cost_usd": "" if rec.cost_usd is None else rec.cost_usd,
            "latency_s": "" if rec.latency_s is None else round(rec.latency_s, 2),
            "attempts": rec.attempts, "raw_path": rec.raw_path, "timestamp": rec.timestamp,
        })
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({_CSV_RENAME.get(k, k): v for k, v in row.items()})


def score(extracted, reference, tolerance: float | None = None, answer_type: str | None = None) -> bool:
    """Original one-function API: True if `extracted` matches `reference` (numbers within `tolerance`).

    The type is guessed from `reference` when not given: a number, or a string that reads as a number, is
    scored as a number; anything else as text.
    """
    if extracted is None:
        return False
    if answer_type is None:
        is_num = (isinstance(reference, (int, float)) and not isinstance(reference, bool)) or (
            isinstance(reference, str) and parse_number(reference) is not None and reference.strip()[:1] in "-+.0123456789$")
        answer_type = "number" if is_num else "text"
    return is_correct(answer_type, reference, extracted, tolerance)[0]
