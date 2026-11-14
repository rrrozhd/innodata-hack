"""Question: counting from the left, which beat is the early, wide one? (synthetic ECG strip, not a clinical case)

A steady strip with exactly one premature beat: it arrives early (at 0.6 of the usual spacing), is wider and taller, has no
P wave, and is followed by a longer pause. The answer is its position among the beats, counting from the left starting at 1
(stored in the data as the beat whose type is "pvc"). Exact. No diagnosis is asked or implied.
"""
from __future__ import annotations

import random

from generate.generators._ecg import EcgGenerator


class EcgPrematureBeatGenerator(EcgGenerator):
    name = "ecg_premature_beat"
    question_type = "find the early, wide beat and give its position"
    answer_type = "number"
    exact_answer = True
    rhythm = "pvc"
    rate_range = (110, 150)
    real_world_examples = [
        "Paper ECG rhythm strips, where a beat that comes early and looks different from its neighbours is located and "
        "marked on the strip",
        "Rhythm-strip printouts from bedside monitors, where a single odd-looking beat is pointed out by its position",
    ]
    QUESTIONS = (
        "One beat on this 10-second ECG rhythm strip arrives early and looks wider and taller than the others. Counting "
        "the QRS complexes from the left and starting at 1, which beat is it? Give a whole number.",
        "On this strip, a single beat comes earlier than expected and looks different (wider, taller, no preceding small "
        "bump). Counting the beats from the left with the first beat as 1, what is its position? Give a whole number.",
    )

    def choose(self, data: dict, rng: random.Random) -> bool:
        pv = [k for k, b in enumerate(data["beats"]) if b["type"] == "pvc"]
        if len(pv) != 1 or len(data["beats"]) < 14:
            return False
        data["variant"] = rng.randrange(len(self.QUESTIONS))
        return True

    def compute_answer(self, data: dict):
        return next(k for k, b in enumerate(data["beats"]) if b["type"] == "pvc") + 1

    def make_question(self, data: dict) -> str:
        return self.QUESTIONS[data["variant"]]
