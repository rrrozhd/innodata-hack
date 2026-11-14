"""Question: at what minute does one panel's reading first cross a stated threshold? (synthetic monitoring chart)

Synthetic monitoring-data reading task, not a clinical case. The threshold is a round number half-way along the
panel's trend. The answer is the minute of the FIRST recorded reading beyond it, found by scanning the saved
readings. Patients are rejected unless the threshold is clearly more than the noise away from both the flat start
and the plateau, so noise cannot create an early false crossing.
"""
from __future__ import annotations

import random

from generate.generators._vitals import VitalsGenerator, noise_bound

THRESHOLD_STEP = {"heart_rate": 5, "spo2": 1, "resp_rate": 1, "sbp": 5, "temp": 0.5}


def first_crossing(data: dict, panel: dict) -> int | None:
    up = panel["delta"] > 0
    for tm, v in zip(data["times"], panel["values"]):
        if v is not None and (v > data["threshold"] if up else v < data["threshold"]):
            return tm
    return None


class VitalsThresholdGenerator(VitalsGenerator):
    name = "vitals_threshold"
    enabled = False  # switched off (not deleted): these four were solved by both models, see README
    question_type = "find when a threshold is crossed"
    answer_type = "number"
    tolerance = None  # set per task by tolerance_for()
    level_offset = 2
    real_world_examples = [
        "Early-warning scoring on hospital observation charts, where a reading crossing a set band triggers a "
        "response (for example the Royal College of Physicians' NEWS2 chart)",
        "Alarm limits on bedside patient monitors, which flag the moment a vital sign passes a set value",
    ]
    QUESTIONS = (
        "In panel {letter} ({name}), at what minute is the first recorded reading {direction} {threshold:g} {unit}?",
        "At what minute does {name} (panel {letter}) first go {direction} {threshold:g} {unit}?",
        "Looking at panel {letter}, find the first reading of {name} that is {direction} {threshold:g} {unit}. "
        "At what minute was it taken?",
    )

    def choose(self, data: dict, rng: random.Random) -> bool:
        options = [i for i, p in enumerate(data["panels"]) if p["trend"] != "flat"]
        if not options:
            return False
        idx = rng.choice(options)
        p = data["panels"][idx]
        step = THRESHOLD_STEP[p["key"]]
        threshold = round(round((p["base"] + 0.5 * p["delta"]) / step) * step, 1)
        bound = noise_bound(p, data["noise_mult"])
        # clearly more than the noise away from the flat start AND from the plateau (so no early false crossing)
        if abs(threshold - p["base"]) < 2 * bound or abs(p["base"] + p["delta"] - threshold) < 2 * bound:
            return False
        data["threshold"], data["target"] = threshold, idx
        data["variant"] = rng.randrange(len(self.QUESTIONS))
        if first_crossing(data, p) is None:
            return False
        return True

    def compute_answer(self, data: dict):
        return first_crossing(data, data["panels"][data["target"]])

    def tolerance_for(self, data: dict) -> float:
        return float(max(2 * data["time_step"], round(0.06 * data["duration"])))

    def make_question(self, data: dict) -> str:
        p = data["panels"][data["target"]]
        return self.QUESTIONS[data["variant"]].format(
            letter=p["letter"], name=p["name"].lower(), unit=p["unit"], threshold=data["threshold"],
            direction="above" if p["delta"] > 0 else "below")
