"""HARD question: read one panel's value at a stated minute, to within one unit.

Synthetic monitoring-data reading task, not a clinical case. Five dense panels with tight, differently scaled axes. The
minute asked about is always one of the labelled gridline minutes and has a recorded reading, and the answer is that
stored reading. The tolerance is one unit of the panel (0.1 for temperature), i.e. at least 4 pixels on the picture, so a
careful person with the gridlines gets it and a rough look does not.
"""
from __future__ import annotations

import random

from generate.generators import _vitals as vt
from generate.generators._vitals import HardVitalsGenerator


class VitalsExactReadingGenerator(HardVitalsGenerator):
    name = "vitals_exact_reading"
    enabled = False  # switched off (not deleted): the live tasks are the ECG strips (ecg_*.py)
    question_type = "read an exact value at a stated minute"
    answer_type = "number"
    tolerance = None  # set per task by tolerance_for()
    panels = 5
    missing = 0.1
    axis_mode = "tight"
    level_offset = 2
    real_world_examples = [
        "Hospital vital-signs observation charts, where a nurse reads the value plotted at a particular observation time "
        "(for example the Royal College of Physicians' NEWS2 observation chart)",
        "Bedside patient-monitor trend screens, read at a marked time to confirm a value before acting on it",
    ]
    QUESTIONS = (
        "What is the value of {name} in panel {letter} at minute {minute}, in {unit}?",
        "Read panel {letter} ({name}) at minute {minute}. What value does it show, in {unit}?",
    )

    def choose(self, data: dict, rng: random.Random) -> bool:
        ticks = set(vt._x_ticks(data["duration"]))
        idx = rng.randrange(len(data["panels"]))
        p = data["panels"][idx]
        options = [tm for tm, v in zip(data["times"], p["values"]) if v is not None and tm in ticks and 0 < tm < data["duration"]]
        if not options:
            return False
        data["target"], data["minute"] = idx, rng.choice(options)
        data["variant"] = rng.randrange(len(self.QUESTIONS))
        return True

    def compute_answer(self, data: dict):
        p = data["panels"][data["target"]]
        return p["values"][data["times"].index(data["minute"])]

    def tolerance_for(self, data: dict) -> float:
        return float(data["panels"][data["target"]]["quantum"])

    def make_question(self, data: dict) -> str:
        p = data["panels"][data["target"]]
        return self.QUESTIONS[data["variant"]].format(letter=p["letter"], name=p["name"].lower(), unit=p["unit"],
                                                      minute=data["minute"])
