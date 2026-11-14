"""Image dependence: the same question words must have a different correct answer
when the visible evidence changes.

A twin scene is built that has exactly the same question text but different
content in the image. Both answers are read from pixels by the independent
solver. Limit: this shows the question alone does not fix the answer; it does
not measure how often a model could guess. The blind baseline in eval/ does that.
"""
from __future__ import annotations

from factory.core import registry
from factory.core.canvas import load_rgb
from factory.core.seed import Reject


def twin(record: dict, data: dict, family, parsed) -> tuple:
    scene = data["scene"]
    prog = registry.program(scene["program"])
    try:
        other = prog.twin(scene)
    except Reject as e:
        return False, f"no twin scene could be built ({e})"
    if prog.question(other) != record["question"]:
        return False, "the twin scene does not have the same question text"
    png, layout = registry.render(other)
    twin_answer = family.answer(family.read(load_rgb(png), layout), scene["program"], parsed, {"scene": other})
    tol = record.get("tolerance_abs") or 0
    if twin_answer == record["answer"] or (tol and abs(twin_answer - record["answer"]) <= tol):
        return False, "the twin image has the same answer"
    return True, f"same question text, different image, different answer ({twin_answer})"
