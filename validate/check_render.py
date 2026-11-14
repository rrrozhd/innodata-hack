"""Pipeline step 4: check that the rendered image matches the structured data.

Placeholder only. The real checks depend on the rendering spec, which is not defined yet.
"""

from pathlib import Path
from typing import Any


def check_render(task: dict[str, Any], image_path: Path) -> bool:
    """Return True if the image at image_path matches task["data"].

    TODO: implement the render-vs-data comparison.
    """
    raise NotImplementedError("TODO: implement render check")
