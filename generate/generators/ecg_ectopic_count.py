"""Question: how many premature (early, wide) beats are on the strip? (synthetic ECG strip, not a clinical case)

A fast, noisy, otherwise steady strip with 3-5 premature beats: each arrives early (0.6 of the usual spacing), is wider and
taller, has no P wave and an inverted T, and is followed by a longer pause; two are never next to each other. The answer is the
number of beats whose type is "pvc" in the data. Exact count. No diagnosis is asked or implied.
"""
from __future__ import annotations

import random

from generate.generators._ecg import EcgGenerator


class EcgEctopicCountGenerator(EcgGenerator):
    name = "ecg_ectopic_count"
    question_type = "count the early, wide beats"
    answer_type = "number"
    exact_answer = True
    rhythm = "ectopy"
    rate_range = (100, 140)
    real_world_examples = [
        "Paper ECG rhythm strips, where beats that arrive early and look different from their neighbours are counted on the strip",
        "Rhythm-strip printouts from bedside monitors, where the number of odd-looking beats in a fixed window is counted by eye",
    ]
    QUESTIONS = (
        "Some beats on this 10-second ECG rhythm strip arrive early and look wider and taller than the others (no small "
        "bump before them). How many such early, wide beats are there? Give a whole number.",
        "Count the beats on this strip that come earlier than expected and look different from the rest (wider, taller, "
        "no preceding small bump). How many are there? Give a whole number.",
    )

    def choose(self, data: dict, rng: random.Random) -> bool:
        n = sum(1 for b in data["beats"] if b["type"] == "pvc")
        if n < 3 or len(data["beats"]) < 14:
            return False
        data["variant"] = rng.randrange(len(self.QUESTIONS))
        return True

    def compute_answer(self, data: dict):
        return sum(1 for b in data["beats"] if b["type"] == "pvc")

    def make_question(self, data: dict) -> str:
        return self.QUESTIONS[data["variant"]]
