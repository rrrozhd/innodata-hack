"""The answer must be the only valid one, must not be given away by the question,
and the stored fingerprints must be consistent with it."""
from __future__ import annotations

import re


def not_in_question(record: dict) -> tuple:
    """The reference answer may not appear in the question text."""
    ans = record["answer"]
    items = ans if isinstance(ans, list) else [ans]
    q = record["question"].lower()
    for item in items:
        s = str(item).lower()
        if len(s) >= 3 and re.search(r"(?<![0-9a-z])" + re.escape(s) + r"(?![0-9a-z])", q):
            return False, f"the answer {item!r} appears in the question text"
    return True, "the answer does not appear in the question text"


def fingerprints(record: dict, data: dict) -> tuple:
    """Armed traps differ from the answer, disarmed ones equal it, and armed
    strategies are pairwise distinct so one wrong answer names one strategy."""
    fps = data["scene"].get("fingerprints", [])
    if not fps:
        return False, "the task has no fingerprint table"
    continuous = record.get("tolerance_abs") is not None or record.get("tolerance_rel") is not None
    seen = {}
    for f in fps:
        if f["armed"] == (f["answer"] == record["answer"]):
            return False, f"fingerprint {f['id']} has the wrong armed flag"
        if f["armed"] and f["kind"] == "strategy" and not continuous:
            key = repr(f["answer"])
            if key in seen:
                return False, f"strategies {seen[key]} and {f['id']} share a wrong answer"
            seen[key] = f["id"]
    armed = sum(1 for f in fps if f["armed"])
    note = ("near-equal readings are labelled ambiguous" if continuous else "strategies pairwise distinct")
    return True, f"{armed} armed and {len(fps) - armed} disarmed fingerprints, {note}"
