"""Question: which panel begins its steady change earliest? (synthetic patient-monitoring chart)

Synthetic monitoring-data reading task, not a clinical case. The same question is asked every time; only the chart
and the answer change. Patients are rejected unless the earliest changing panel starts clearly before the next one
(at least 3 readings and 10 minutes earlier). Flat panels never count. Answers are panel letters, which are printed
in each panel's title, so the answer is a letter the model can read straight off the picture.
"""
from __future__ import annotations

import random

from generate.generators._vitals import VitalsGenerator


class VitalsFirstChangeGenerator(VitalsGenerator):
    name = "vitals_first_change"
    enabled = False  # switched off (not deleted): these four were solved by both models, see README
    question_type = "compare when panels start to change"
    answer_type = "choice"
    level_offset = 3
    real_world_examples = [
        "Hospital vital-signs observation charts, where staff compare which vital sign began to drift first "
        "(for example the Royal College of Physicians' NEWS2 observation chart)",
        "Multi-parameter bedside patient-monitor trend screens, read side by side to see the order in which "
        "readings change",
    ]
    question_template = (
        "Some panels stay flat all the way through; the others stay flat for a while and then begin a steady "
        "change. Among the panels that change, which panel's steady change begins earliest? Reply with the panel "
        "letter."
    )
    fixed_question = True

    def choose(self, data: dict, rng: random.Random) -> bool:
        changing = sorted((p["onset"], i) for i, p in enumerate(data["panels"]) if p["trend"] != "flat")
        if len(changing) < 2:
            return False
        if changing[1][0] - changing[0][0] < max(10, 3 * data["time_step"]):
            return False
        data["target"] = changing[0][1]
        return True

    def compute_answer(self, data: dict):
        return min((p["onset"], p["letter"]) for p in data["panels"] if p["trend"] != "flat")[1]
