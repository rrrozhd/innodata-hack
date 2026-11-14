"""Label a wrong answer with the trap it is consistent with.

A match means the answer equals what that shortcut would produce on this task.
It does not prove the model used that method; the report puts every match rate
next to the rate expected by chance, and armed/disarmed task pairs give the
stronger evidence.
"""
from __future__ import annotations

from .score import canon, fmt_of, same

NO_ANSWER, UNMATCHED = "no_answer", "unmatched"


def label(record: dict, data: dict, parsed, correct: int) -> tuple:
    """Return (fingerprint, fingerprint_kind) for one scored reply.

    parsed is the canonical answer from score(), or None.
    """
    if correct:
        return "", ""
    if parsed is None:
        return NO_ANSWER, ""
    fmt = fmt_of(record)
    hits = [f for f in data["scene"].get("fingerprints", [])
            if f["armed"] and same(parsed, canon(f["answer"], fmt), record)]
    if not hits:
        return UNMATCHED, ""
    if len(hits) == 1:
        return hits[0]["id"], hits[0]["kind"]
    kinds = sorted({f["kind"] for f in hits})
    return "ambiguous:" + "|".join(f["id"] for f in hits), kinds[0] if len(kinds) == 1 else "mixed"
