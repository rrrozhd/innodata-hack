"""HARD question: how many times does a noisy line cross a dashed reference line?

Synthetic monitoring-data reading task, not a clinical case. One flat panel is drawn with a dashed horizontal reference
line just beside its average level, so the noisy line weaves across it many times. The answer is the number of times
consecutive readings lie on opposite sides of the reference value, counted from the saved readings (no readings are
missing in this task). Patients are redrawn unless: the count is between 8 and 22, no reading equals the reference
value, and every reading is at least 3 pixels from the dashed line, so no crossing is a judgment call.
"""
from __future__ import annotations

import random

from generate.generators import _vitals as vt
from generate.generators._vitals import HardVitalsGenerator


def count_crossings(values: list, ref: float) -> int:
    seen = [v > ref for v in values if v is not None]
    return sum(1 for a, b in zip(seen, seen[1:]) if a != b)


class VitalsCrossingCountGenerator(HardVitalsGenerator):
    name = "vitals_crossing_count"
    enabled = False  # switched off (not deleted): the live tasks are the ECG strips (ecg_*.py)
    question_type = "count crossings of a reference line"
    answer_type = "number"
    exact_answer = True
    panels = 4
    missing = 0.0
    axis_mode = "tight"
    level_offset = 1
    real_world_examples = [
        "Alarm limits on bedside patient monitors, where a reading hovering around a limit crosses it again and again "
        "and staff look at how many times it was crossed",
        "Early-warning scoring on hospital observation charts, where a reading near a band edge flips in and out of the "
        "band between observations",
    ]
    QUESTIONS = (
        "Panel {letter} ({name}) has a dashed horizontal reference line. How many times does the {name} line cross the "
        "dashed line? Count each time two consecutive readings are on opposite sides of it. Give a whole number.",
        "In panel {letter}, the {name} readings wander around the dashed reference line. How many times does the line "
        "go from one side of the dashed line to the other? Give a whole number.",
    )

    def choose(self, data: dict, rng: random.Random) -> bool:
        flat = [i for i, p in enumerate(data["panels"]) if p["trend"] == "flat"]
        if not flat:
            return False
        idx = rng.choice(flat)
        p = data["panels"][idx]
        q = p["quantum"]
        ref = round(p["base"] + rng.choice([0.5, 1.5, 2.5]) * q * rng.choice([-1, 1]), 3)
        vals = [v for v in p["values"] if v is not None]
        if any(v == ref for v in vals):
            return False
        box = vt.layout(data)["panels"][idx]
        px_per_unit = (box["y1"] - box["y0"]) / (p["y_max"] - p["y_min"])
        if min(abs(v - ref) for v in vals) * px_per_unit < 3 or not p["y_min"] < ref < p["y_max"]:
            return False
        if not 8 <= count_crossings(p["values"], ref) <= 22:
            return False
        data["target"], data["ref"] = idx, {"panel": idx, "value": ref}
        data["variant"] = rng.randrange(len(self.QUESTIONS))
        return True

    def compute_answer(self, data: dict):
        return count_crossings(data["panels"][data["target"]]["values"], data["ref"]["value"])

    def make_question(self, data: dict) -> str:
        p = data["panels"][data["target"]]
        return self.QUESTIONS[data["variant"]].format(letter=p["letter"], name=p["name"].lower())
