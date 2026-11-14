"""Score an extracted answer against the reference answer.

Placeholder only. The comparison rule (exact match, numeric tolerance, etc.) is not decided yet.
"""

from typing import Any


def score(extracted: str | None, reference: Any) -> bool:
    """Return True if the extracted answer matches the reference.

    TODO: implement the comparison rule.
    """
    raise NotImplementedError("TODO: score answer")
