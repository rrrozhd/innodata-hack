"""Legibility checks, measured on the delivered PNG and judged at the size a
model is likely to see.

SCALE is the assumed worst-case provider downscale. The two evaluated models'
real resize policies are not published to us, so this is a stated assumption.
"""
from __future__ import annotations

import numpy as np

from factory.core.canvas import CAP_HEIGHT

SCALE = 0.667
MIN_CAP_PX = 7.0          # capital-letter height after downscaling
MIN_TEXT_CONTRAST = 4.5   # WCAG AA for text the answer depends on
MIN_OTHER_CONTRAST = 3.0  # other text and graphical marks
CRITICAL_ROLES = ("train", "station", "time")


def _lum(rgb) -> float:
    def f(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (f(float(v)) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def labels(img: np.ndarray, layout: dict) -> tuple:
    """Glyph size, measured contrast, ink actually present, and no overlaps."""
    boxes = []
    for lab in layout["labels"]:
        x0, y0, x1, y1 = lab["bbox"]
        if x0 < 0 or y0 < 0 or x1 > layout["width"] or y1 > layout["height"]:
            return False, f"label {lab['text']!r} runs off the image"
        cap = lab["font_px"] * CAP_HEIGHT * SCALE
        if cap < MIN_CAP_PX:
            return False, f"label {lab['text']!r} would be {cap:.1f} px tall after downscaling"
        crop = img[int(y0):int(np.ceil(y1)), int(x0):int(np.ceil(x1))].astype(np.int16)
        dist = np.abs(crop - np.array(lab["bg"], dtype=np.int16)).max(axis=2)
        if (dist > 60).sum() < max(12, 0.03 * dist.size):
            return False, f"label {lab['text']!r} has almost no ink in the image"
        ink = crop.reshape(-1, 3)[int(dist.argmax())]
        need = MIN_TEXT_CONTRAST if lab["role"] in CRITICAL_ROLES else MIN_OTHER_CONTRAST
        got = contrast(ink, lab["bg"])
        if got < need:
            return False, f"label {lab['text']!r} has contrast {got:.1f}, needs {need}"
        boxes.append((x0, y0, x1, y1, lab["text"]))
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                return False, f"labels {a[4]!r} and {b[4]!r} overlap"
    return True, (f"{len(boxes)} labels: capital height at least {MIN_CAP_PX} px at {SCALE}x, contrast measured "
                  "in the PNG, none overlapping")
