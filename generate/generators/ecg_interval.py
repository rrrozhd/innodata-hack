"""Question: how many large boxes lie between two named beats (5-8 beats apart)? (synthetic ECG strip, not a clinical case)

A fast, irregular strip with at least 18 beats. The question names two beats 5-8 positions apart by position (counting from the left, first beat = 1); the
answer is the spacing between their R peaks in large boxes (5 mm = 0.2 s), from the stored beat positions, rounded to two
decimals. Tolerance 0.5 large box (2.5 mm, 15 pixels). Needs reading the grid, not just counting.
"""
from __future__ import annotations

import random

from generate.generators._ecg import FS, EcgGenerator


class EcgIntervalGenerator(EcgGenerator):
    name = "ecg_interval"
    question_type = "measure the spacing between two beats on the grid"
    answer_type = "number"
    tolerance = None  # set per task by tolerance_for()
    rhythm = "irregular"
    rate_range = (95, 140)
    real_world_examples = [
        "Paper ECG rhythm strips, where the spacing between two beats is measured in grid boxes",
        "Rhythm-strip printouts from bedside monitors, where intervals are measured on the grid with calipers or by counting boxes",
    ]
    QUESTIONS = (
        "This ECG strip uses the standard grid (25 mm/s; each large box, the darker 5 mm square, is 0.2 s). How many large "
        "boxes are there between the peak of beat {a} and the peak of beat {b}, counting beats from the left and starting at 1? "
        "Give a number with up to two decimals.",
        "Measure on the grid (large box = 5 mm = 0.2 s) the distance from the tall peak of beat {a} to the tall peak of beat "
        "{b} (beats counted from the left, first beat = 1). How many large boxes is it?",
    )

    def choose(self, data: dict, rng: random.Random) -> bool:
        n = len(data["beats"])
        if n < 18:
            return False
        span = rng.randint(5, 8)
        a = rng.randrange(2, n - span - 1)  # 1-based position of the first of the two beats
        data["pair"] = [a, a + span]
        data["variant"] = rng.randrange(len(self.QUESTIONS))
        return True

    def compute_answer(self, data: dict):
        a, b = data["pair"]
        gap = data["beats"][b - 1]["i"] - data["beats"][a - 1]["i"]
        return round(gap / FS / 0.2, 2)

    def tolerance_for(self, data: dict) -> float:
        return 0.5

    def make_question(self, data: dict) -> str:
        a, b = data["pair"]
        return self.QUESTIONS[data["variant"]].format(a=a, b=b)
