"""Pull the final answer out of a model reply.

The prompt tells the model to end with a line `FINAL ANSWER: <answer>`. We take the LAST such
line. If it is missing, extraction fails (returns None) and the response is scored wrong with a
note; we deliberately do not guess from the rest of the text.
"""
from __future__ import annotations

import re

_FINAL = re.compile(r"final\s*answer\s*[:\-]\s*(.+)", re.IGNORECASE)


def extract_final_answer(text: str | None) -> str | None:
    if not text:
        return None
    matches = _FINAL.findall(text)
    if not matches:
        return None
    answer = matches[-1].strip().rstrip(".").strip()  # trailing full stop
    answer = answer.strip("*_`").strip()  # markdown bold/code wrappers
    answer = re.sub(r"^\$?\\?boxed\{(.*)\}\$?$", r"\1", answer)  # \boxed{...}
    return answer.rstrip(".").strip() or None


def extract_answer(raw_text: str | None) -> str | None:
    """Original name for extract_final_answer: the final answer from a raw reply, or None if absent."""
    return extract_final_answer(raw_text)
