"""Tolerance fairness for percentage answers.

The percentages asked for are computed exactly from the numbers printed on the
chart, so the tolerance only has to absorb rounding. It is fair if it is at least
the rounding step, and if no predicted wrong method lands inside it.
"""
from __future__ import annotations

ROUNDING_STEP = 0.01   # answers are asked for to two decimal places


def check(record: dict, data: dict) -> tuple:
    tol = record.get("tolerance_abs")
    if record["answer_type"] != "number" or tol is None:
        return True, "the answer is exact (a count, a train number or a list of train numbers); no tolerance needed"
    if tol < ROUNDING_STEP:
        return False, f"tolerance {tol:g} is tighter than the rounding step {ROUNDING_STEP:g}"
    for f in data["scene"].get("fingerprints", []):
        if f["armed"] and abs(float(f["answer"]) - float(record["answer"])) <= tol:
            return False, f"wrong method {f['id']} lands inside the tolerance"
    return True, (f"tolerance {tol:g} covers rounding of an exactly computed value, and every predicted wrong "
                  "answer is outside it")
