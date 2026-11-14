"""Scoring. Formatting is normalised; content never is.

`canon` turns an answer string into one canonical form per answer format, and
the same function is applied to the reference, to the model's answer and to
every fingerprint, so all three are compared on equal terms.
"""
from __future__ import annotations

import re

_SEQ_SPLIT = re.compile(r"\s*(?:->|→|,|;|>|\|)\s*")


def _label(s: str) -> str:
    s = str(s).lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9$#.\-_ ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip(" .")


def canon(value, fmt: str):
    """Canonical form, or None when the text is not a valid answer of this format."""
    if value is None:
        return None
    if isinstance(value, list):
        if fmt in ("pin_seq", "pin_set"):
            pins = [canon(v, "pin") for v in value]
            return None if None in pins else (sorted(pins) if fmt == "pin_set" else pins)
        items = [_label(v) for v in value]
        if fmt == "name_set":
            items = sorted(items)
        return items if all(items) else None
    s = str(value).strip()
    if not s:
        return None
    if fmt == "hex_byte":
        m = re.search(r"0[xX]([0-9a-fA-F]{1,2})(?![0-9a-fA-F])", s)
        if not m:  # the question asks for hex, so two bare hex digits are read as hex
            m = re.fullmatch(r"([0-9a-fA-F]{2})[hH]?", s)
        return f"0x{int(m.group(1), 16):02X}" if m else None
    if fmt == "int":
        m = re.fullmatch(r"[^\d+\-−]*([+\-−]?\d[\d,]*)(?:\.0+)?\D*", s)
        return int(m.group(1).replace(",", "").replace("−", "-")) if m else None
    if fmt == "number":
        m = re.search(r"[+\-−]?(?:\d+\.?\d*|\.\d+)(?:[eE][+\-]?\d+)?", s.replace(",", ""))
        return float(m.group(0).replace("−", "-")) if m else None
    if fmt in ("pin", "pin_seq", "pin_set"):
        # J4-7, J4 pin 7, j4.7 and J4:7 are the same pin
        pins = [f"{c.upper()}-{int(p)}" for c, p in re.findall(r"\b([A-Za-z]{1,3}\d{1,2})\s*(?:[-–.:]|pin)?\s*(\d{1,3})\b",
                                                              s, flags=re.IGNORECASE)]
        if not pins:
            return None
        return pins[0] if fmt == "pin" else (sorted(pins) if fmt == "pin_set" else pins)
    if fmt in ("sequence", "name_set"):
        seps = re.compile(r"\s*(?:,|;|\n)\s*") if fmt == "name_set" else _SEQ_SPLIT
        items = [_label(p) for p in seps.split(s) if p.strip()]
        if fmt == "name_set":
            items = sorted(i for i in items if i not in ("none", "(none)"))
        return items or None
    return _label(s) or None


def fmt_of(record: dict) -> str:
    return record.get("answer_format") or {"integer": "int", "number": "number", "sequence": "sequence"}.get(
        record["answer_type"], "label")


def same(a, b, record: dict) -> bool:
    """Do two canonical answers count as the same for this task?"""
    if a is None or b is None:
        return False
    if fmt_of(record) == "number":
        if record.get("tolerance_abs") is not None:
            return abs(a - b) <= record["tolerance_abs"] + 1e-12
        if record.get("tolerance_rel") is not None:
            return abs(a - b) <= record["tolerance_rel"] * abs(b) + 1e-12
        return abs(a - b) <= 1e-9
    return a == b


def score(parsed_text: str, record: dict) -> tuple:
    """Return (correct 0/1, canonical parsed answer or None)."""
    fmt = fmt_of(record)
    got, ref = canon(parsed_text, fmt), canon(record["answer"], fmt)
    return (1 if same(got, ref, record) else 0), got
