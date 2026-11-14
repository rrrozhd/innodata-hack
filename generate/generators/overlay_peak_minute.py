"""HARD question: at what minute does one named line reach its highest reading, among six overlapping lines?

Synthetic monitoring-data reading task, not a clinical case. The answer is the minute of the series' largest saved
reading. Charts are redrawn unless that reading is unique and beats the series' second-highest reading by at least 0.8
percentage points, and is not at the first or last reading. The tolerance is one reading interval (2 minutes).
"""
from __future__ import annotations

import random

from generate.generators._overlay import OverlayGenerator


class OverlayPeakMinuteGenerator(OverlayGenerator):
    name = "overlay_peak_minute"
    enabled = False  # switched off (not deleted): the live tasks are the ECG strips (ecg_*.py)
    question_type = "find the minute of one line's peak among overlapping lines"
    answer_type = "number"
    tolerance = None  # set per task by tolerance_for()
    real_world_examples = [
        "Bedside patient-monitor trend screens, where staff look for the moment one signal peaked while others overlap it",
        "Hospital charts overlaying several vital signs, used to find when a particular measurement was at its worst",
    ]
    QUESTIONS = (
        "Six lines share this chart. At what minute does the {name} line (letter {letter}) reach its highest point?",
        "Follow the {name} line (letter {letter}) on the shared chart. At which minute is it at its maximum?",
    )

    def choose(self, data: dict, rng: random.Random) -> bool:
        k = rng.randrange(6)
        vals = data["series"][k]["values"]
        order = sorted(range(len(vals)), key=lambda i: -vals[i])
        top, second = order[0], order[1]
        if vals[top] - vals[second] < 0.8 or top in (0, len(vals) - 1):
            return False
        data["target"], data["variant"] = k, rng.randrange(len(self.QUESTIONS))
        return True

    def compute_answer(self, data: dict):
        vals = data["series"][data["target"]]["values"]
        return data["times"][max(range(len(vals)), key=lambda i: vals[i])]

    def tolerance_for(self, data: dict) -> float:
        return float(data["time_step"])

    def make_question(self, data: dict) -> str:
        s = data["series"][data["target"]]
        return self.QUESTIONS[data["variant"]].format(name=s["name"].lower(), letter=s["letter"])
