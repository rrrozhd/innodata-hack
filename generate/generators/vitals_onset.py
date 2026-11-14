"""Question: at what minute does the steady trend in one panel begin? (synthetic patient-monitoring chart)

Synthetic monitoring-data reading task, not a clinical case. The answer is the trend's onset minute stored in the
saved data (the line is flat apart from noise until then); the tolerance is a few readings wide because a person
(or model) can only see the bend to within a few minutes.
"""
from __future__ import annotations

import random

from generate.generators._vitals import VitalsGenerator


class VitalsOnsetGenerator(VitalsGenerator):
    name = "vitals_onset"
    enabled = False  # switched off (not deleted): these four were solved by both models, see README
    question_type = "read when a trend starts"
    answer_type = "number"
    tolerance = None  # set per task by tolerance_for()
    level_offset = 0
    real_world_examples = [
        "Hospital vital-signs observation charts that plot a patient's readings over time so staff can see when a "
        "trend begins (for example the Royal College of Physicians' NEWS2 observation chart)",
        "Bedside patient-monitor trend screens showing heart rate, oxygen saturation, respiratory rate and blood "
        "pressure against time",
    ]
    QUESTIONS = (
        "In panel {letter} ({name}), the line stays flat apart from small random noise and then begins a steady "
        "change. At what minute does that steady change begin?",
        "Panel {letter} shows {name}. Ignoring the small random wobble, at what minute does the line first start "
        "to trend steadily up or down?",
        "At what minute does the {name} line in panel {letter} leave its flat starting level and begin a "
        "sustained trend?",
    )

    def choose(self, data: dict, rng: random.Random) -> bool:
        changing = [i for i, p in enumerate(data["panels"]) if p["trend"] != "flat"]
        if not changing:
            return False
        data["target"] = rng.choice(changing)
        data["variant"] = rng.randrange(len(self.QUESTIONS))
        return True

    def compute_answer(self, data: dict):
        return data["panels"][data["target"]]["onset"]

    def tolerance_for(self, data: dict) -> float:
        return float(max(2 * data["time_step"], round(0.06 * data["duration"])))

    def make_question(self, data: dict) -> str:
        p = data["panels"][data["target"]]
        return self.QUESTIONS[data["variant"]].format(letter=p["letter"], name=p["name"].lower())
