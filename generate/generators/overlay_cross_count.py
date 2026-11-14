"""HARD question: how many times do two named lines cross each other on a crowded shared chart?

Synthetic monitoring-data reading task, not a clinical case. Six noisy series overlap on one axis. The answer is the
number of times the two chosen series swap places between consecutive readings (the sign of their difference changes),
counted from the saved readings. A tie (difference exactly 0) after minute 0 is never allowed, and the count must be
between 8 and 22. An exact count: a careful person can follow both colours with the legend, models usually cannot.
"""
from __future__ import annotations

import random

from generate.generators._overlay import COLORS, OverlayGenerator


def count_crossings(a: list, b: list) -> int | None:
    diffs = [x - y for x, y in zip(a[1:], b[1:])]
    if any(d == 0 for d in diffs):
        return None
    return sum(1 for p, q in zip(diffs, diffs[1:]) if (p > 0) != (q > 0))


class OverlayCrossCountGenerator(OverlayGenerator):
    name = "overlay_cross_count"
    enabled = False  # switched off (not deleted): the live tasks are the ECG strips (ecg_*.py)
    question_type = "count crossings between two overlapping lines"
    answer_type = "number"
    exact_answer = True
    real_world_examples = [
        "Multi-parameter bedside patient-monitor trend screens, where several vital signs are overlaid and staff look at "
        "when and how often two of them swap places",
        "Hospital charts that overlay heart rate and blood pressure on one set of axes so the point where the two lines "
        "cross can be seen",
    ]
    QUESTIONS = (
        "Six lines are drawn on the same chart. How many times does the {a} line (letter {la}) cross the {b} line "
        "(letter {lb})? Count each time the two lines swap places between consecutive readings. Give a whole number.",
        "On this chart, lines {la} ({a}) and {lb} ({b}) repeatedly pass through each other. How many times do they "
        "cross? Give a whole number.",
    )

    def choose(self, data: dict, rng: random.Random) -> bool:
        i, j = rng.sample(range(6), 2)
        n = count_crossings(data["series"][i]["values"], data["series"][j]["values"])
        if n is None or not 8 <= n <= 22:
            return False
        data["pair"], data["variant"] = [i, j], rng.randrange(len(self.QUESTIONS))
        return True

    def compute_answer(self, data: dict):
        i, j = data["pair"]
        return count_crossings(data["series"][i]["values"], data["series"][j]["values"])

    def make_question(self, data: dict) -> str:
        sa, sb = (data["series"][k] for k in data["pair"])
        return self.QUESTIONS[data["variant"]].format(a=sa["name"].lower(), b=sb["name"].lower(), la=sa["letter"], lb=sb["letter"])
