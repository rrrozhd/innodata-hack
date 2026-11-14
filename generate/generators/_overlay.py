"""Shared core for the OVERLAY vital-sign chart generators (overlay_*.py).

SYNTHETIC monitoring-data reading tasks: not clinical cases, not clinically validated, nothing here is a diagnosis. Six
made-up series are drawn on ONE shared axis as "% change from the first reading", so the lines pile up, overlap and
cross each other constantly. Some pairs of colours are deliberately similar (two blues, a red and an orange). Each
series is a flat start, then a steady change (sometimes with a partial recovery), plus seeded noise, rounded to 0.1.
Answers come from the saved readings only; `verify_png` re-measures the picture to prove it shows that data.
Pillow only; randomness only from the `rng` you are handed.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from generate.base import CheckResult, TaskGenerator

NAMES = ["Heart rate", "Oxygen saturation", "Respiratory rate", "Systolic blood pressure", "Diastolic blood pressure",
         "Temperature"]
LETTERS = "ABCDEF"
COLORS = [(31, 119, 180), (120, 190, 235), (214, 39, 40), (255, 127, 14), (44, 160, 44), (148, 103, 189)]
GRIDS = [(180, 2), (240, 2)]  # 91 or 121 readings per series
W, ML, MR, MT, MB = 1100, 84, 24, 96, 56
PLOT_H = 470
GRID, AXIS, BG = (222, 222, 222), (0, 0, 0), (255, 255, 255)
TICKS = [0.5, 1, 2, 2.5, 5]


def _nice(lo: float, hi: float) -> tuple[float, float, float]:
    for tick in TICKS:
        y0, y1 = math.floor(lo / tick) * tick, math.ceil(hi / tick) * tick
        if 5 <= round((y1 - y0) / tick) <= 10:
            return y0, y1, tick
    return math.floor(lo / 5) * 5, math.ceil(hi / 5) * 5, 5


def clean(s: dict, t: float) -> float:
    """Noise-free % change of a series at minute t."""
    if s["trend"] == "flat":
        return 0.0
    on, pk, amp = s["onset"], s["peak_t"], s["amp"]
    if t <= on:
        return 0.0
    if t <= pk:
        return amp * (t - on) / (pk - on)
    if s["trend"] == "ramp_recover":
        rs, re = s["rec_start"], s["rec_end"]
        if t <= rs:
            return amp
        if t <= re:
            return amp * (1 - 0.7 * (t - rs) / (re - rs))
        return amp * 0.3
    return amp


def build_overlay(rng: random.Random, index: int) -> dict:
    duration, step = rng.choice(GRIDS)
    times = list(range(0, duration + 1, step))
    noise = round(rng.uniform(0.9, 1.5), 2)
    series = []
    for i in range(6):
        trend = rng.choice(["flat", "ramp", "ramp", "ramp_recover"])
        s = {"letter": LETTERS[i], "name": NAMES[i], "trend": trend, "amp": 0.0, "onset": None, "peak_t": None}
        if trend != "flat":
            s["amp"] = round(rng.uniform(2.5, 8.0) * rng.choice([-1, 1]), 1)
            ramp = int(round(rng.uniform(0.2, 0.35) * duration / step)) * step
            rec = int(round(rng.uniform(0.15, 0.25) * duration / step)) * step if trend == "ramp_recover" else 0
            hold = int(round(0.06 * duration / step)) * step if rec else 0
            lo = int(round(0.1 * duration / step)) * step
            hi = max(lo, int(round((0.9 * duration - ramp - hold - rec) / step)) * step)
            s["onset"] = rng.randrange(lo, hi + 1, step)
            s["peak_t"] = s["onset"] + ramp
            if rec:
                s["rec_start"], s["rec_end"] = s["peak_t"] + hold, s["peak_t"] + hold + rec
        vals = [0.0]
        for tm in times[1:]:
            vals.append(round(clean(s, tm) + max(-2.5, min(2.5, rng.gauss(0, 1))) * noise, 1))
        s["values"] = vals
        series.append(s)
    allv = [v for s in series for v in s["values"]]
    y_min, y_max, tick = _nice(min(allv) - 0.5, max(allv) + 0.5)
    return {"duration": duration, "time_step": step, "times": times, "noise": noise, "series": series,
            "y_min": y_min, "y_max": y_max, "tick": tick}


def geometry(data: dict) -> dict:
    return {"width": W, "height": MT + PLOT_H + MB, "x0": ML, "x1": W - MR, "y0": MT, "y1": MT + PLOT_H}


def _x(d, g, m):
    return int(round(g["x0"] + (g["x1"] - g["x0"]) * m / d["duration"]))


def _y(d, g, v):
    return int(round(g["y1"] - (g["y1"] - g["y0"]) * (v - d["y_min"]) / (d["y_max"] - d["y_min"])))


def x_ticks(duration: int) -> list[int]:
    for step in (10, 15, 20, 30, 60):
        if 6 <= duration / step <= 13:
            return list(range(0, duration + 1, step))
    return list(range(0, duration + 1, 30))


def _font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def render_png(d: dict, out_path: Path) -> None:
    from PIL import Image, ImageDraw
    g = geometry(d)
    img = Image.new("RGB", (g["width"], g["height"]), BG)
    draw = ImageDraw.Draw(img)
    small, title = _font(15), _font(17)
    for k, s in enumerate(d["series"]):  # legend: 3 per row, colour sample + letter + name
        lx, ly = ML + (k % 3) * 330, 14 + (k // 3) * 30
        draw.line([(lx, ly + 9), (lx + 34, ly + 9)], fill=COLORS[k], width=3)
        draw.text((lx + 42, ly), f'{s["letter"]}  {s["name"]}', fill=AXIS, font=title)
    draw.text((ML, MT - 24), "% change from the first reading", fill=AXIS, font=small)
    y = d["y_min"]
    while y <= d["y_max"] + 1e-9:
        py = _y(d, g, y)
        draw.line([(g["x0"], py), (g["x1"], py)], fill=GRID, width=1)
        label = f"{y:+g}" if y else "0"
        draw.text((g["x0"] - 8 - draw.textlength(label, font=small), py - 8), label, fill=AXIS, font=small)
        y = round(y + d["tick"], 6)
    for m in x_ticks(d["duration"]):
        px = _x(d, g, m)
        draw.line([(px, g["y0"]), (px, g["y1"])], fill=GRID, width=1)
        draw.line([(px, g["y1"]), (px, g["y1"] + 4)], fill=AXIS, width=1)
        draw.text((px - draw.textlength(str(m), font=small) / 2, g["y1"] + 6), str(m), fill=AXIS, font=small)
    draw.line([(g["x0"], g["y0"]), (g["x0"], g["y1"]), (g["x1"], g["y1"])], fill=AXIS, width=2)
    for k, s in enumerate(d["series"]):
        pts = [(_x(d, g, tm), _y(d, g, v)) for tm, v in zip(d["times"], s["values"])]
        draw.line(pts, fill=COLORS[k], width=2, joint="curve")
    label = "Time (minutes)"
    draw.text(((W - draw.textlength(label, font=title)) / 2, g["y1"] + 28), label, fill=AXIS, font=title)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")


def _near(d: dict, g: dict, o: dict, i: int, x: int, want: int) -> bool:
    """True when series `o` has a line within 4 pixels of (x, want) for some pixel column x-2..x+2 (it is on top)."""
    lo, hi = max(0, i - 1), min(len(d["times"]) - 1, i + 1)
    pts = [(_x(d, g, d["times"][n]), _y(d, g, o["values"][n])) for n in range(lo, hi + 1)]
    for xx in range(x - 2, x + 3):
        for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
            if xa <= xx <= xb and xb > xa and abs(ya + (yb - ya) * (xx - xa) / (xb - xa) - want) <= 4:
                return True
    return False


def verify_png(d: dict, image_path: Path) -> CheckResult:
    """Every reading must have its series colour near its place; a reading may be hidden only if another series' line
    passes within 3 pixels of the same spot (overlap), never otherwise."""
    from PIL import Image
    try:
        img = Image.open(image_path).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        return CheckResult.fail(f"cannot open the image ({exc})", where=str(image_path))
    g = geometry(d)
    if img.size != (g["width"], g["height"]):
        return CheckResult.fail("the image size does not match the layout", expected=(g["width"], g["height"]),
                                actual=img.size, where=str(image_path))
    px = img.load()
    bad, hidden, total = [], 0, 0
    for k, s in enumerate(d["series"]):
        for i, (tm, v) in enumerate(zip(d["times"], s["values"])):
            x, want = _x(d, g, tm), _y(d, g, v)
            total += 1
            found = any(px[xx, yy] == COLORS[k] for xx in range(x - 2, x + 3) for yy in range(want - 3, want + 4)
                        if 0 <= xx < g["width"] and 0 <= yy < g["height"])
            if found:
                continue
            top = {c for n, c in enumerate(COLORS) if n != k}
            covered = px[x, want] in top or any(px[xx, yy] in top for xx in range(x - 1, x + 2) for yy in range(want - 1, want + 2)) \
                or any(_near(d, g, o, i, x, want) for j, o in enumerate(d["series"]) if j != k)
            if covered:
                hidden += 1
            else:
                bad.append(f'series {s["letter"]} minute {tm}: reading {v} is not on the line (y={want})')
    if bad:
        return CheckResult.fail(f"{len(bad)} reading(s) do not match the picture: " + "; ".join(bad[:3]), where=str(image_path))
    return CheckResult.ok(f"all {total} readings in 6 overlapping series match the picture ({hidden} hidden only by overlap)")


class OverlayGenerator(TaskGenerator):
    """Base for overlay_* generators (in a module starting with `_`, so never registered itself)."""
    visual_type = "overlaid patient vital-signs trend chart (synthetic)"
    image_ext = "png"
    enabled = True

    def render(self, data: dict, out_path: Path) -> None:
        render_png(data, out_path)

    def verify_render(self, data: dict, image_path: Path) -> CheckResult:
        return verify_png(data, image_path)

    def choose(self, data: dict, rng: random.Random) -> bool:
        return True

    def generate_data(self, rng: random.Random, index: int) -> dict:
        for _ in range(3000):
            data = build_overlay(rng, index)
            if self.choose(data, rng):
                return data
        raise RuntimeError(f"no overlay chart suits {self.name} after 3000 tries")
