"""Question: what is the last recorded value in one panel? (synthetic patient-monitoring chart)

Synthetic monitoring-data reading task, not a clinical case. The answer is the final reading stored in the saved data
(the last reading is never missing). The tolerance is 3% of that panel's axis range, so it scales with the axis.
"""
from __future__ import annotations

import math
import random

from generate.generators._vitals import VitalsGenerator


class VitalsFinalValueGenerator(VitalsGenerator):
    name = "vitals_final_value"
    enabled = False  # switched off (not deleted): these four were solved by both models, see README
    question_type = "read the last value"
    answer_type = "number"
    tolerance = None  # set per task by tolerance_for()
    level_offset = 1
    real_world_examples = [
        "Hospital vital-signs observation charts, where the most recent plotted reading is what staff read off first "
        "(for example the Royal College of Physicians' NEWS2 observation chart)",
        "Bedside patient-monitor trend screens showing the latest value of each vital sign at the right-hand edge",
    ]
    QUESTIONS = (
        "What is the last recorded value of {name} in panel {letter}, in {unit}?",
        "Read the final point of the {name} line in panel {letter}. What is its value in {unit}?",
        "At the end of the recording, what value does panel {letter} ({name}) show, in {unit}?",
    )

    def choose(self, data: dict, rng: random.Random) -> bool:
        data["target"] = rng.randrange(len(data["panels"]))
        data["variant"] = rng.randrange(len(self.QUESTIONS))
        return True

    def compute_answer(self, data: dict):
        return data["panels"][data["target"]]["values"][-1]

    def tolerance_for(self, data: dict) -> float:
        p = data["panels"][data["target"]]
        q = p["quantum"]
        return round(max(q, math.ceil(0.03 * (p["y_max"] - p["y_min"]) / q - 1e-9) * q), 1)

    def make_question(self, data: dict) -> str:
        p = data["panels"][data["target"]]
        return self.QUESTIONS[data["variant"]].format(letter=p["letter"], name=p["name"].lower(), unit=p["unit"])
