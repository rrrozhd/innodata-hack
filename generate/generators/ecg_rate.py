"""Question: what is the heart rate in beats per minute? (synthetic ECG strip, not a clinical case)

A steady, fast strip (beat spacing varies by at most 4 ms). The answer is 60 divided by the mean beat-to-beat interval in the
stored data, rounded to a whole number; tolerance 3 beats per minute. The strip is exactly 10 seconds on the standard
25 mm/s grid, so the rate can be got by counting beats (x6) or by measuring the spacing on the grid.
"""
from __future__ import annotations

import random

from generate.generators._ecg import FS, EcgGenerator


class EcgRateGenerator(EcgGenerator):
    name = "ecg_rate"
    question_type = "work out the heart rate from the strip"
    answer_type = "number"
    tolerance = 3.0
    rhythm = "regular"
    rate_range = (120, 190)
    real_world_examples = [
        "Paper ECG rhythm strips, where the rate is worked out from the beat spacing on the grid or from the number of "
        "beats in a fixed time window",
        "Rhythm-strip printouts from bedside monitors, where the rate on the strip is checked against the displayed value",
    ]
    QUESTIONS = (
        "This is a steady 10-second ECG rhythm strip (standard grid: 25 mm/s, large box = 0.2 s). Using the spacing "
        "between consecutive beats (not by counting the beats in the strip and multiplying), what is the heart rate in "
        "beats per minute?",
        "Measure the spacing between consecutive beats on the grid (25 mm/s; each large box is 0.2 s) and work out the "
        "heart rate shown on this steady strip, in beats per minute. Do not use the count of beats in the 10 seconds.",
    )

    def choose(self, data: dict, rng: random.Random) -> bool:
        data["variant"] = rng.randrange(len(self.QUESTIONS))
        return len(data["beats"]) >= 7

    def compute_answer(self, data: dict):
        idx = [b["i"] for b in data["beats"]]
        gaps = [b - a for a, b in zip(idx, idx[1:])]
        return round(60 * FS / (sum(gaps) / len(gaps)))

    def make_question(self, data: dict) -> str:
        return self.QUESTIONS[data["variant"]]
