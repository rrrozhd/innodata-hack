"""The generator contract. THIS IS THE ONLY FILE TEAMMATES NEED TO UNDERSTAND.

A generator makes one family of visual tasks (e.g. "bus timetable chart"). Subclass
`TaskGenerator`, fill in the four REQUIRED methods, optionally override the two OPTIONAL hooks,
and save the file in generate/generators/. The shell does everything else: calling your code,
saving data and images, validating, calling the models, scoring, and reporting.

Pipeline mapping (the five steps from the brief):
    1. generate_data(rng, index)   -> structured data (a JSON-able dict)
    2. render(data, out_path)      -> draws the image from that data
    3. compute_answer(data)        -> the reference answer, computed in code
    4. verify_render(data, image)  -> OPTIONAL: prove the image really shows the data
    5. question_needs_image(...)   -> OPTIONAL: prove the question can't be answered from text
    (make_question(data) writes the question text.)

Rules the shell enforces for you:
    * Everything must be deterministic given the `rng` you are handed. Do NOT use the global
      `random` / `numpy.random` state, and do not use the clock. Use `rng` (a random.Random).
      For numpy: numpy.random.default_rng(rng.randrange(2**32)).
    * render() and compute_answer() only ever see the data dict that was SAVED to disk.
    * If the answer is numeric and `tolerance` is set, the tolerance sentence is added to the
      question automatically (the rules require it to be stated in the prompt).
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CheckResult:
    status: str  # "pass" | "fail" | "warn" | "skip"
    message: str
    expected: Any = None
    actual: Any = None
    where: str = ""

    @classmethod
    def ok(cls, message: str) -> "CheckResult":
        return cls("pass", message)

    @classmethod
    def fail(cls, message: str, expected: Any = None, actual: Any = None, where: str = "") -> "CheckResult":
        return cls("fail", message, expected, actual, where)

    @classmethod
    def warn(cls, message: str, expected: Any = None, actual: Any = None, where: str = "") -> "CheckResult":
        return cls("warn", message, expected, actual, where)

    @classmethod
    def skip(cls, message: str) -> "CheckResult":
        return cls("skip", message)


def format_tolerance(tol: float) -> str:
    return f"{tol:g}"


def task_seed(base_seed: int, generator_name: str, index: int) -> int:
    """Stable per-task seed (same inputs -> same seed on every machine)."""
    digest = hashlib.sha256(f"{base_seed}|{generator_name}|{index}".encode()).hexdigest()
    return int(digest[:12], 16)


def to_jsonable(obj: Any) -> Any:
    """Convert numpy/pandas scalars and arrays (and tuples) into plain JSON types."""
    import json

    def default(o):
        for attr in ("tolist", "item"):
            if hasattr(o, attr):
                return getattr(o, attr)()
        if isinstance(o, (set, frozenset)):
            return sorted(o)
        if isinstance(o, Path):
            return str(o)
        raise TypeError(f"Cannot save {type(o).__name__} to JSON; convert it to a plain type")

    return json.loads(json.dumps(obj, default=default, sort_keys=True))


class TaskGenerator:
    # ---- identity: fill these in as class attributes --------------------------------------
    name: str = ""  # unique, snake_case, e.g. "bus_timetable"
    visual_type: str = ""  # short label for the kind of picture, e.g. "line chart"
    question_type: str = ""  # short label for the kind of question, e.g. "read peak value"
    answer_type: str = "number"  # "number" | "text" | "choice"
    tolerance: float | None = None  # numeric answers: accepted +/- (None = exact match)
    real_world_examples: list[str] = []  # CITE 1-2 real examples behind this visual (rules)
    image_ext: str = "png"
    enabled: bool = True  # False = discovered but skipped (use while half-finished)

    # ---- REQUIRED ---------------------------------------------------------------------------
    def generate_data(self, rng: random.Random, index: int) -> dict:
        """Step 1. Return the structured data (plain dict/list/str/number). Use ONLY `rng`."""
        raise NotImplementedError("TODO: implement generate_data()")

    def render(self, data: dict, out_path: Path) -> None:
        """Step 2. Draw the image from `data` and save it to `out_path`."""
        raise NotImplementedError("TODO: implement render()")

    def compute_answer(self, data: dict) -> Any:
        """Step 3. Compute the reference answer IN CODE from `data` (never by hand)."""
        raise NotImplementedError("TODO: implement compute_answer()")

    def make_question(self, data: dict) -> str:
        """The question shown to the model. Must NOT reveal the data the image shows."""
        raise NotImplementedError("TODO: implement make_question()")

    # ---- OPTIONAL hooks (strongly recommended: they are judged as 'verifiable tasks') -------
    def verify_render(self, data: dict, image_path: Path) -> CheckResult:
        """Step 4. Check the picture really encodes `data` (e.g. re-read bar heights from pixels).

        Return CheckResult.ok(...) or CheckResult.fail(msg, expected=..., actual=...).
        """
        return CheckResult.warn(
            "verify_render() is not implemented for this generator (step 4: check the rendering "
            "against the data)"
        )

    def question_needs_image(self, data: dict, question: str) -> CheckResult:
        """Step 5. Generator-specific proof the question can't be answered without the image.

        The shell already runs a generic 'does the question text leak the data' check.
        """
        return CheckResult.skip("no generator-specific needs-image check (generic leak check only)")

    # ---- helpers the shell uses (don't override) --------------------------------------------
    def build_question(self, data: dict) -> str:
        question = self.make_question(data).strip()
        if self.answer_type == "number" and self.tolerance is not None:
            tol = format_tolerance(self.tolerance)
            if tol not in question:
                question += f" Answers within +/-{tol} of the correct value are accepted."
        return question

    def coerce_answer(self, raw: Any) -> Any:
        if self.answer_type == "number":
            value = float(raw)
            return int(value) if value.is_integer() else value
        if self.answer_type == "choice":
            return str(raw).strip().upper()
        return str(raw).strip()

    def implemented(self) -> dict[str, bool]:
        base = TaskGenerator
        return {
            m: getattr(type(self), m) is not getattr(base, m)
            for m in ("generate_data", "render", "compute_answer", "make_question",
                      "verify_render", "question_needs_image")
        }
