"""Wrong-answer fingerprints.

A fingerprint is a wrong answer the generator can predict: either a whole wrong
method ("strategy") or the right method with one local error ("single_slip").
Each task stores its table so that a wrong model answer can be labelled with the
trap it is consistent with.
"""
from __future__ import annotations

from .seed import Reject

STRATEGY = "strategy"
SINGLE_SLIP = "single_slip"


def entry(fid: str, kind: str, description: str, answer) -> dict:
    return {"id": fid, "kind": kind, "description": description, "answer": answer}


def add_unique(out: list, reference, fid: str, description: str, answer) -> None:
    """Add a strategy unless an earlier one already gives this same wrong answer.

    Two mistakes that land on the same value by construction (for example
    ignoring an enable line, and using the wrong one) cannot be told apart from
    the answer, so only the first, more general one is kept.
    """
    if answer == reference or all(e["answer"] != answer for e in out):
        out.append(entry(fid, STRATEGY, description, answer))


def finalize(entries: list, reference, expect: dict | None = None, tol: float = 0.0) -> list:
    """Mark each entry armed or disarmed and enforce the separability rules.

    - armed: its answer differs from the reference;
    - disarmed: its answer equals the reference exactly (the shortcut happens to
      be a correct reading of this scene);
    - for exact answers, armed strategies must be pairwise distinct, so one
      wrong answer points to one strategy; otherwise the scene is rejected and
      resampled;
    - for a continuous reading (`tol` > 0), a wrong method whose result lands
      within twice the tolerance of the reference would be accepted or too
      close to call, so the scene is rejected. Two strategies may land near each
      other; the labeller then reports the match as ambiguous;
    - a single slip that lands on a strategy's answer, or inside that band, is
      dropped.

    `expect` maps fingerprint id -> required armed state (True/False); a scene
    that does not meet it is rejected. This is how armed/disarmed pairs are made.
    """
    def close(a, b, k=1.0):
        if tol:
            return abs(float(a) - float(b)) <= k * tol + 1e-12
        return a == b

    out, strategies = [], []
    for e in entries:
        e = dict(e)
        e["armed"] = e["answer"] != reference
        if tol and e["armed"] and close(e["answer"], reference, 2.0):
            if e["kind"] == SINGLE_SLIP:
                continue
            raise Reject(f"wrong method {e['id']} lands inside or next to the tolerance band")
        if expect and e["id"] in expect and expect[e["id"]] is not None and e["armed"] != expect[e["id"]]:
            raise Reject(f"fingerprint {e['id']} armed={e['armed']}, wanted {expect[e['id']]}")
        if e["kind"] == STRATEGY and e["armed"]:
            if not tol:
                for other in strategies:
                    if e["answer"] == other["answer"]:
                        raise Reject(f"strategies {other['id']} and {e['id']} give the same wrong answer")
            strategies.append(e)
        out.append(e)
    kept, slips = [], []
    for e in out:
        if e["kind"] == SINGLE_SLIP:
            if not e["armed"] or any(close(e["answer"], o["answer"], 2.0) for o in strategies + slips):
                continue
            slips.append(e)
        kept.append(e)
    return kept


def chance_match_rate(fingerprints: list, answer_space: int) -> float:
    """Share of wrong answers that would match some armed fingerprint by luck."""
    distinct = {repr(f["answer"]) for f in fingerprints if f["armed"]}
    return len(distinct) / max(answer_space - 1, 1)
