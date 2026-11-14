"""Pull the final answer out of a model reply."""
from __future__ import annotations

import re

_FINAL = re.compile(r"FINAL\s+ANSWER\s*[:：]\s*(.*)", re.IGNORECASE)


def final_answer(reply: str) -> str:
    """Text after the last 'FINAL ANSWER:' marker, with markdown decoration
    removed. Empty if the reply has no such line."""
    found = _FINAL.findall(reply or "")
    if not found:
        return ""
    s = found[-1].strip()
    s = re.sub(r"^[`*_\s]+|[`*_\s]+$", "", s)
    return s.strip()
