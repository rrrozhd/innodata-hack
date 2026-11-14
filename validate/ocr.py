"""OCR read-back of labels with Tesseract, on a downscaled copy of the PNG.

Tesseract is installed in the Docker image. On a machine without it the check
reports "skipped"; with REQUIRE_OCR=1 (set in the Dockerfile) a skip is a failure.
"""
from __future__ import annotations

import difflib
import io
import os
import re
import shutil

from PIL import Image, ImageOps

from .legibility import SCALE

MIN_SIMILARITY = 0.8
ROLES = ("train", "station", "time")
MAX_LABELS = 36   # on dense drawings an even sample is read, plus every label the answer depends on


def available() -> bool:
    if not shutil.which("tesseract"):
        return False
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return True


def _norm(s: str) -> str:
    s = s.replace("−", "-").replace("µ", "u").replace("μ", "u")
    return re.sub(r"[^a-z0-9#+\-.]", "", s.lower().replace("_", ""))


def read_labels(png: bytes, layout: dict, needed: set) -> tuple:
    """Return (status, detail). `needed` are label texts the answer depends on;
    each of those must read back, plus at least 90% of all checked labels."""
    if not available():
        if os.environ.get("REQUIRE_OCR") == "1":
            return "fail", "Tesseract is required here but is not installed"
        return "skipped", "Tesseract not installed on this machine; OCR runs in the Docker image"
    import pytesseract

    full = Image.open(io.BytesIO(png)).convert("RGB")
    small = full.resize((round(full.width * SCALE), round(full.height * SCALE)), Image.LANCZOS)
    results, bad_needed = [], []
    pool = [lab for lab in layout["labels"] if lab["role"] in ROLES]
    if len(pool) > MAX_LABELS:
        step = len(pool) / MAX_LABELS
        keep = {int(k * step) for k in range(MAX_LABELS)}
        pool = [lab for i, lab in enumerate(pool) if i in keep or lab["text"] in needed]
    for lab in pool:
        x0, y0, x1, y1 = (v * SCALE for v in lab["bbox"])
        crop = small.crop((int(x0) - 4, int(y0) - 4, int(x1) + 5, int(y1) + 5)).convert("L")
        if sum(lab["bg"]) < 384:  # light text on dark: invert so the glyphs are dark
            crop = ImageOps.invert(crop)
        crop = crop.resize((crop.width * 5, crop.height * 5), Image.BICUBIC)
        text = pytesseract.image_to_string(crop, config="--psm 7").strip()
        sim = difflib.SequenceMatcher(None, _norm(text), _norm(lab["text"])).ratio()
        results.append(sim >= MIN_SIMILARITY)
        if lab["text"] in needed and sim < MIN_SIMILARITY:
            bad_needed.append(f"{lab['text']!r} read as {text!r}")
    share = sum(results) / max(len(results), 1)
    if bad_needed:
        return "fail", "labels the answer depends on did not read back: " + "; ".join(bad_needed)
    if share < 0.9:
        return "fail", f"only {share:.0%} of labels read back at {SCALE}x"
    return "pass", f"{sum(results)} of {len(results)} labels read back by OCR at {SCALE}x"
