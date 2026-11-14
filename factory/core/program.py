"""Skeleton shared by every question program.

A program splits its randomness in two streams:
- structure: everything that fixes the wording of the question (who and what is
  on the chart, how it is laid out, which entity the question is about);
- data: the numbers on the chart.
A twin scene keeps the structure and redraws the data, so it has exactly the same
question text and a different answer. That is how image dependence is shown.
"""
from __future__ import annotations

from . import fingerprints as fp
from .seed import Reject, rng_for

DATA_TRIES = 60
TWIN_TRIES = 25


class Program:
    family = ""
    name = ""
    answer_type = "label"
    answer_format = "label"
    answer_space = 100
    variants = ("",)

    # ---- to implement ----------------------------------------------------------------------------------------
    def structure(self, rs, level: int, variant: str) -> dict:
        raise NotImplementedError

    def fill(self, rd, st: dict, level: int) -> dict:
        """Return the scene body built from the structure and the data stream."""
        raise NotImplementedError

    def question(self, scene: dict) -> str:
        raise NotImplementedError

    def solve(self, scene: dict):
        raise NotImplementedError

    def fingerprints(self, scene: dict, answer) -> list:
        raise NotImplementedError

    def check(self, scene: dict, answer) -> None:
        """Fairness conditions on the finished scene; raise Reject to redraw the data."""

    def tolerance(self, scene: dict) -> float:
        return float(scene.get("record_extra", {}).get("tolerance_abs", 0.0))

    # ---- shared ----------------------------------------------------------------------------------------------
    def build(self, seed: int, level: int, attempt: int = 0, variant: str = "", twin: int = 0) -> dict:
        rs = rng_for(seed, self.name, level, attempt, "struct")
        st = self.structure(rs, level, variant)
        last = None
        for data_try in range(DATA_TRIES):
            rd = rng_for(seed, self.name, level, attempt, "data", twin, data_try)
            try:
                scene = {"family": self.family, "program": self.name, "level": level, "seed": seed,
                         "attempt": attempt, "variant": variant}
                scene.update(self.fill(rd, st, level))
                answer = self.solve(scene)
                self.check(scene, answer)
                scene["fingerprints"] = fp.finalize(self.fingerprints(scene, answer), answer, st.get("expect"),
                                                    self.tolerance(scene))
                return scene
            except Reject as e:
                last = e
        raise Reject(f"no valid data for this structure ({last})")

    def twin(self, scene: dict) -> dict:
        """Same structure and question text, different numbers, and a different answer."""
        answer, tol = self.solve(scene), self.tolerance(scene)
        for k in range(1, TWIN_TRIES + 1):
            try:
                other = self.build(scene["seed"], scene["level"], scene["attempt"], scene["variant"], twin=k)
            except Reject:
                continue
            got = self.solve(other)
            if (abs(got - answer) > 2 * tol) if tol else (got != answer):
                return other
        raise Reject("no twin scene with a different answer")
