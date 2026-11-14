"""HARD question: how many separate gaps (runs of missing readings) does one panel's line have?

Synthetic monitoring-data reading task, not a clinical case. Five panels, high noise, and about one reading in eight is
missing, so the line is broken into many pieces. The answer is the number of maximal runs of missing readings in that
panel's saved readings (the first and last readings are never missing). It is an exact count: a careful person can
count the breaks, but models tend to lose count. Patients are redrawn unless the count is between 9 and 20.
"""
from __future__ import annotations

import random

from generate.generators._vitals import HardVitalsGenerator


def count_gaps(values: list) -> int:
    gaps, inside = 0, False
    for v in values:
        if v is None and not inside:
            gaps += 1
        inside = v is None
    return gaps


class VitalsGapCountGenerator(HardVitalsGenerator):
    name = "vitals_gap_count"
    enabled = False  # switched off (not deleted): the live tasks are the ECG strips (ecg_*.py)
    question_type = "count the breaks in a line"
    answer_type = "number"
    exact_answer = True
    panels = 5
    missing = 0.12
    level_offset = 0
    real_world_examples = [
        "Bedside patient-monitor trend screens, where a dropped sensor shows as a break in the line and staff check how "
        "often a signal was lost",
        "Hospital vital-signs observation charts where missed observations leave gaps that are counted when auditing "
        "how often a patient was actually observed",
    ]
    QUESTIONS = (
        "Some readings in panel {letter} ({name}) are missing, which breaks the line. How many separate gaps (breaks "
        "in the line where one or more readings are missing) does the {name} line in panel {letter} have? Count each "
        "break once. Give a whole number.",
        "In panel {letter}, the {name} line is interrupted wherever readings are missing. Counting every separate break "
        "once (a break may span several missing readings), how many breaks are there? Give a whole number.",
    )

    def choose(self, data: dict, rng: random.Random) -> bool:
        data["target"] = rng.randrange(len(data["panels"]))
        data["variant"] = rng.randrange(len(self.QUESTIONS))
        return 9 <= count_gaps(data["panels"][data["target"]]["values"]) <= 20

    def compute_answer(self, data: dict):
        return count_gaps(data["panels"][data["target"]]["values"])

    def make_question(self, data: dict) -> str:
        p = data["panels"][data["target"]]
        return self.QUESTIONS[data["variant"]].format(letter=p["letter"], name=p["name"].lower())
