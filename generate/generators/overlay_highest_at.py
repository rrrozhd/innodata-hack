"""HARD question: which of six overlapping lines is highest at a stated minute?

Synthetic monitoring-data reading task, not a clinical case. The minute is a labelled gridline minute. The answer is the
letter of the series with the largest saved reading at that minute. Charts are redrawn unless the winner leads the
runner-up by at least 0.7 percentage points (about 13 pixels), so it is never a photo finish. A letter answer.
"""
from __future__ import annotations

import random

from generate.generators import _overlay as ov
from generate.generators._overlay import OverlayGenerator


class OverlayHighestAtGenerator(OverlayGenerator):
    name = "overlay_highest_at"
    enabled = False  # switched off (not deleted): the live tasks are the ECG strips (ecg_*.py)
    question_type = "pick the highest of six overlapping lines at a minute"
    answer_type = "choice"
    fixed_question = False
    real_world_examples = [
        "Multi-parameter bedside patient-monitor trend screens, read at a marked time to see which signal sits highest",
        "Hospital charts that overlay several vital signs on one axis, compared at a single observation time",
    ]
    QUESTIONS = (
        "Six lines share this chart (letters A to F, see the legend). At minute {minute}, which line is highest? Reply "
        "with the letter.",
        "Look at minute {minute} on the shared chart. Which of the six lines has the largest value there? Reply with "
        "the letter A-F.",
    )

    def choose(self, data: dict, rng: random.Random) -> bool:
        ticks = [m for m in ov.x_ticks(data["duration"]) if 0 < m < data["duration"] and m in data["times"]]
        m = rng.choice(ticks)
        i = data["times"].index(m)
        ranked = sorted(((s["values"][i], s["letter"]) for s in data["series"]), reverse=True)
        if ranked[0][0] - ranked[1][0] < 0.7:
            return False
        data["minute"], data["variant"] = m, rng.randrange(len(self.QUESTIONS))
        return True

    def compute_answer(self, data: dict):
        i = data["times"].index(data["minute"])
        return max((s["values"][i], s["letter"]) for s in data["series"])[1]

    def make_question(self, data: dict) -> str:
        return self.QUESTIONS[data["variant"]].format(minute=data["minute"])
