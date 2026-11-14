"""Pipeline step 5: verify that the question cannot be answered without the image.

Placeholder only. The method for this check (for example, a text-only control run) is not decided yet.
"""

from typing import Any


def needs_image(task: dict[str, Any]) -> bool:
    """Return True if the question requires the image to answer.

    TODO: choose and implement the check method.
    """
    raise NotImplementedError("TODO: implement needs-image check")
