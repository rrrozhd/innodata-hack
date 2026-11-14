"""Question: how many QRS complexes (heartbeats) are in the 10-second strip? (synthetic ECG strip, not a clinical case)

A very fast, irregular, noisy strip with 24-36 beats. The answer is the number of beats stored in the data (every beat is fully
inside the strip, and the selftest finds them again from the raw samples with separate code). Exact count.
"""
from __future__ import annotations

import random

from generate.generators._ecg import EcgGenerator


class EcgBeatCountGenerator(EcgGenerator):
    name = "ecg_beat_count"
    question_type = "count the heartbeats on a strip"
    answer_type = "number"
    exact_answer = True
    rhythm = "irregular"
    rate_range = (150, 200)
    real_world_examples = [
        "Paper ECG rhythm strips that are scanned, faxed or photographed, where staff count the beats (QRS complexes) "
        "on the strip to estimate a rate",
        "Rhythm-strip printouts from bedside monitors, where the beats in a fixed time window are counted by eye",
    ]
    QUESTIONS = (
        "This is a 10-second ECG rhythm strip (standard grid: 25 mm/s). How many QRS complexes (heartbeats) are on the "
        "strip? Give a whole number.",
        "Count the heartbeats (the tall, narrow QRS complexes) on this 10-second strip. How many are there? Give a whole "
        "number.",
    )

    def choose(self, data: dict, rng: random.Random) -> bool:
        data["variant"] = rng.randrange(len(self.QUESTIONS))
        return 24 <= len(data["beats"]) <= 36

    def compute_answer(self, data: dict):
        return len(data["beats"])

    def make_question(self, data: dict) -> str:
        return self.QUESTIONS[data["variant"]]
