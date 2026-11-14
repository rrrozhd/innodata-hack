"""Checks that the delivered PNG is what the stored data says it is."""
from __future__ import annotations

import json

from factory.core import registry
from factory.core.canvas import file_sha256, pixel_sha256


def hashes(record: dict, png: bytes) -> tuple:
    ok = record.get("image_sha256") == file_sha256(png) and record.get("pixel_sha256") == pixel_sha256(png)
    return ok, "file and pixel hashes match the task record" if ok else "PNG does not match the recorded hashes"


def rerender(data: dict, png: bytes) -> tuple:
    """The image must be a pure function of the stored scene: render it again
    and compare pixels and layout."""
    png2, layout2 = registry.render(data["scene"])
    same_pixels = pixel_sha256(png2) == pixel_sha256(png)
    same_layout = json.dumps(layout2, sort_keys=True) == json.dumps(data["layout"], sort_keys=True)
    if same_pixels and same_layout:
        return True, "re-rendering the stored scene reproduces the PNG pixel for pixel"
    return False, f"re-render differs (pixels equal: {same_pixels}, layout equal: {same_layout})"
