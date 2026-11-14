"""Shared core for the synthetic patient-monitoring chart generators (vitals_*.py).

THESE ARE SYNTHETIC MONITORING-DATA READING TASKS. They are not clinical cases, they are not clinically validated,
and nothing here is a diagnosis or medical advice. The patients are made up from simple rules and a seed.

What a task is: a stack of panels (A, B, C, D), one vital sign per panel, drawn over time. Each panel is a flat
line, or a flat start followed by a steady change, sometimes followed by a partial recovery, plus seeded noise and
some missing readings. A question asks the model to read the chart (when does a trend start, what is the last value,
when is a threshold crossed, which panel changes first). The answer is computed from the saved DATA, never from the
picture, and `verify_png` re-measures the picture's pixels to prove the picture shows exactly that data.

Level dial (1-10, written into the data): number of panels (2 -> 4), noise (x0.5 -> x1.5), missing readings
(none up to level 2, then up to ~10%), and a longer, denser time axis. Axis scales vary from panel to panel.
Stdlib + Pillow only (Pillow is in requirements.txt); randomness comes only from the `rng` you are handed.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from generate.base import CheckResult, TaskGenerator

# ---- the vital signs (made-up ranges for synthetic data; "worsen" is the direction the line drifts) ----------
VARIABLES: dict[str, dict] = {
    "heart_rate": {"name": "Heart rate", "unit": "bpm", "base": (62, 88), "worsen": +1, "drift": (25, 55),
                   "noise": 1.5, "wide": (40, 160), "quantum": 1},
    "spo2": {"name": "Oxygen saturation", "unit": "%", "base": (96, 99), "worsen": -1, "drift": (6, 14),
             "noise": 0.4, "wide": (80, 100), "quantum": 1},
    "resp_rate": {"name": "Respiratory rate", "unit": "breaths/min", "base": (12, 18), "worsen": +1, "drift": (8, 16),
                  "noise": 0.6, "wide": (6, 40), "quantum": 1},
    "sbp": {"name": "Systolic blood pressure", "unit": "mmHg", "base": (112, 128), "worsen": -1, "drift": (25, 45),
            "noise": 2.5, "wide": (60, 160), "quantum": 1},
    "temp": {"name": "Temperature", "unit": "°C", "base": (36.5, 37.1), "worsen": +1, "drift": (1.5, 2.8),
             "noise": 0.08, "wide": (35, 41), "quantum": 0.1},
}
# (duration in minutes, minutes between readings); longer/denser grids are used at higher levels
TIME_GRIDS = [(60, 2), (90, 3), (120, 2), (180, 3), (240, 4)]
NOISE_CLIP = 2.5  # noise is a seeded gaussian clipped to +/- this many standard deviations
TICK_CHOICES = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 25, 50]


def level_profile(level: int) -> dict:
    t = (level - 1) / 9
    return {"panels": 2 if level <= 3 else 3 if level <= 7 else 4, "noise_mult": round(0.5 + t, 3),
            "missing": 0.0 if level <= 2 else round((level - 2) * 0.012, 4),
            "grids": TIME_GRIDS[: min(len(TIME_GRIDS), 2 + level // 3)]}


def noise_bound(panel: dict, noise_mult: float) -> float:
    """The most a reading can lie from its clean curve, apart from rounding to the unit."""
    return NOISE_CLIP * panel["noise_sd"]


def _snap(x: float, step: int) -> int:
    return int(round(x / step)) * step


def clean_value(p: dict, t: float) -> float:
    """The noise-free curve of a panel at minute t: flat, then a ramp, then (optionally) a partial recovery."""
    base, delta = p["base"], p["delta"]
    if p["trend"] == "flat":
        return base
    on, pk = p["onset"], p["peak_t"]
    if t <= on:
        return base
    if t <= pk:
        return base + delta * (t - on) / (pk - on)
    if p["trend"] == "worsen_recover":
        rs, re, resid = p["recover_start"], p["recover_end"], p["residual"]
        if t <= rs:
            return base + delta
        if t <= re:
            return base + delta * (1 - (1 - resid) * (t - rs) / (re - rs))
        return base + delta * resid
    return base + delta


def _nice_axis(lo: float, hi: float) -> tuple[float, float, float]:
    for tick in TICK_CHOICES:
        y0 = math.floor(lo / tick + 1e-9) * tick
        y1 = math.ceil(hi / tick - 1e-9) * tick
        if 3 <= round((y1 - y0) / tick) <= 8:
            return round(y0, 6), round(y1, 6), tick
    tick = TICK_CHOICES[-1]
    return math.floor(lo / tick) * tick, math.ceil(hi / tick) * tick, tick


def _trends(rng: random.Random, scenario: str, keys: list[str]) -> list[str]:
    n = len(keys)
    out = ["flat"] * n
    if scenario == "deterioration":
        for i in rng.sample(range(n), rng.randint(2, n)):
            out[i] = "worsen"
    elif scenario == "recovery":
        for i in rng.sample(range(n), rng.randint(1, n)):
            out[i] = "worsen_recover"
        out = [x if x != "flat" else rng.choice(["flat", "worsen"]) for x in out]
    elif scenario == "conflicting":
        i, j = rng.sample(range(n), 2)
        out = [rng.choice(["flat", "worsen", "improve"]) for _ in range(n)]
        out[i], out[j] = "worsen", "improve"
    # an "improve" trend would push oxygen saturation past 100%, so oxygen stays flat instead
    return ["flat" if (tr == "improve" and k == "spo2") else tr for tr, k in zip(out, keys)]


def build_patient(rng: random.Random, level: int, panels: int | None = None, missing: float | None = None,
                  axis_mode: str | None = None, grids: list | None = None) -> dict:
    """One synthetic patient at a difficulty level. Uses ONLY `rng`, so the same rng gives the same patient.
    `panels`, `missing` (chance a reading is missing) and `axis_mode` ("tight") override the level's defaults; the
    draws from `rng` are the same either way, so leaving them out reproduces the earlier patients exactly."""
    prof = level_profile(level)
    if panels:
        prof["panels"] = min(panels, len(VARIABLES))
    if missing is not None:
        prof["missing"] = missing
    if grids:
        prof["grids"] = grids
    duration, step = rng.choice(prof["grids"])
    times = list(range(0, duration + 1, step))
    scenario = rng.choice(["stable", "deterioration", "deterioration", "recovery", "conflicting"])
    keys = rng.sample(list(VARIABLES), prof["panels"])
    trends = _trends(rng, scenario, keys)
    panels = []
    for idx, (key, trend) in enumerate(zip(keys, trends)):
        v = VARIABLES[key]
        q = v["quantum"]
        base = round(rng.uniform(*v["base"]), 1)
        p = {"letter": "ABCDE"[idx], "key": key, "name": v["name"], "unit": v["unit"], "quantum": q,
             "trend": trend, "base": base, "delta": 0.0, "onset": None, "peak_t": None,
             "noise_sd": round(v["noise"] * prof["noise_mult"], 4)}
        if trend != "flat":
            sign = v["worsen"] * (-1 if trend == "improve" else 1)
            mag = rng.uniform(*v["drift"]) * (0.6 if trend == "improve" else 1.0)
            mag = max(mag, 8.5 * p["noise_sd"])  # a trend must always stand clearly above the noise
            p["delta"] = round(sign * mag, 1)
            ramp = _snap(rng.uniform(0.2, 0.35) * duration, step)
            hold = _snap(rng.uniform(0.05, 0.1) * duration, step) if trend == "worsen_recover" else 0
            rec_len = _snap(rng.uniform(0.15, 0.25) * duration, step) if trend == "worsen_recover" else 0
            earliest = max(step, _snap(0.12 * duration, step))
            latest = max(earliest, _snap(0.9 * duration - (ramp + hold + rec_len), step))
            p["onset"] = rng.randrange(earliest, latest + 1, step)
            p["peak_t"] = p["onset"] + ramp
            if trend == "worsen_recover":
                p.update(recover_start=p["peak_t"] + hold, recover_end=p["peak_t"] + hold + rec_len, residual=0.25)
        # measured readings: clean curve + clipped seeded noise, rounded to the unit; some readings missing
        values = []
        missing_run = 0
        for i, tm in enumerate(times):
            noise = max(-NOISE_CLIP, min(NOISE_CLIP, rng.gauss(0, 1))) * p["noise_sd"]
            reading = clean_value(p, tm) + noise
            reading = round(round(reading / q) * q, 1 if q < 1 else 0)
            if v["unit"] == "%":
                reading = min(100, reading)
            is_missing = 0 < i < len(times) - 1 and missing_run < 3 and rng.random() < prof["missing"]
            missing_run = missing_run + 1 if is_missing else 0
            values.append(None if is_missing else (int(reading) if q >= 1 else reading))
        present = [x for x in values if x is not None]
        span = max(max(present) - min(present), v["drift"][0] * 0.8)  # a flat line must not look like a trend
        mode = rng.choice(["tight", "wide"])
        mode = axis_mode or mode
        if mode == "tight":
            pad = 0.15 * span
            lo, hi = min(present) - pad, max(present) + pad
        else:
            lo, hi = min(v["wide"][0], min(present)), max(v["wide"][1], max(present))
        if hi - lo < span:
            mid = (hi + lo) / 2
            lo, hi = mid - span / 2, mid + span / 2
        p["y_min"], p["y_max"], p["tick"] = _nice_axis(lo, hi)
        p["values"] = values
        panels.append(p)
    return {"level": level, "scenario": scenario, "duration": duration, "time_step": step, "times": times,
            "noise_mult": prof["noise_mult"], "panels": panels}


# ---- drawing (Pillow) -------------------------------------------------------------------------------------------
WIDTH, MARGIN_L, MARGIN_R, PANEL_H, PANEL_GAP, TOP, BOTTOM = 1000, 96, 30, 190, 62, 18, 46
COLORS = [(31, 119, 180), (214, 39, 40), (44, 160, 44), (148, 103, 189), (230, 120, 20)]
REF_COLOR = (70, 70, 70)  # the dashed reference line (never one of the panel colours)
GRID, AXIS, BG = (222, 222, 222), (0, 0, 0), (255, 255, 255)


def layout(data: dict) -> dict:
    """Pixel geometry of each panel's plot area (shared by the drawing and the pixel check)."""
    panels = []
    for i, p in enumerate(data["panels"]):
        y0 = TOP + i * (PANEL_H + PANEL_GAP) + 26
        panels.append({"x0": MARGIN_L, "x1": WIDTH - MARGIN_R, "y0": y0, "y1": y0 + PANEL_H})
    return {"width": WIDTH, "height": TOP + len(panels) * (PANEL_H + PANEL_GAP) + BOTTOM - 20, "panels": panels}


def _x(data: dict, box: dict, minute: float) -> int:
    return int(round(box["x0"] + (box["x1"] - box["x0"]) * minute / data["duration"]))


def _y(p: dict, box: dict, value: float) -> int:
    return int(round(box["y1"] - (box["y1"] - box["y0"]) * (value - p["y_min"]) / (p["y_max"] - p["y_min"])))


def _x_ticks(duration: int) -> list[int]:
    for step in (5, 10, 15, 20, 30, 60):
        if 5 <= duration / step <= 12:
            return list(range(0, duration + 1, step))
    return list(range(0, duration + 1, 60))


def _font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)  # scalable built-in font (Pillow >= 10.1): same on every machine
    except TypeError:
        return ImageFont.load_default()


def render_png(data: dict, out_path: Path) -> None:
    """Draw the stacked panels. Missing readings break the line; every reading lies exactly on the line."""
    from PIL import Image, ImageDraw
    lay = layout(data)
    img = Image.new("RGB", (lay["width"], lay["height"]), BG)
    draw = ImageDraw.Draw(img)
    small, title = _font(15), _font(18)
    for i, (p, box) in enumerate(zip(data["panels"], lay["panels"])):
        color = COLORS[i]
        draw.text((box["x0"], box["y0"] - 26), f'{p["letter"]}   {p["name"]} ({p["unit"]})', fill=AXIS, font=title)
        y = p["y_min"]
        while y <= p["y_max"] + 1e-9:  # horizontal grid + y labels
            py = _y(p, box, y)
            draw.line([(box["x0"], py), (box["x1"], py)], fill=GRID, width=1)
            label = f"{y:g}"
            draw.text((box["x0"] - 8 - draw.textlength(label, font=small), py - 8), label, fill=AXIS, font=small)
            y = round(y + p["tick"], 6)
        for m in _x_ticks(data["duration"]):  # vertical grid + minute labels
            px = _x(data, box, m)
            draw.line([(px, box["y0"]), (px, box["y1"])], fill=GRID, width=1)
            draw.line([(px, box["y1"]), (px, box["y1"] + 4)], fill=AXIS, width=1)
            label = str(m)
            draw.text((px - draw.textlength(label, font=small) / 2, box["y1"] + 6), label, fill=AXIS, font=small)
        draw.line([(box["x0"], box["y0"]), (box["x0"], box["y1"]), (box["x1"], box["y1"])], fill=AXIS, width=2)
        ref = data.get("ref")
        if ref and ref["panel"] == i:  # dashed horizontal reference line, drawn under the data
            ry = _y(p, box, ref["value"])
            for dx in range(box["x0"] + 2, box["x1"] - 40, 14):
                draw.line([(dx, ry), (dx + 7, ry)], fill=REF_COLOR, width=2)
            draw.text((box["x1"] - 36, ry - 18), f'{ref["value"]:g}', fill=REF_COLOR, font=small)
        run: list[tuple[int, int]] = []
        for tm, v in list(zip(data["times"], p["values"])) + [(None, None)]:
            if v is not None:
                run.append((_x(data, box, tm), _y(p, box, v)))
                continue
            if len(run) >= 2:
                draw.line(run, fill=color, width=2, joint="curve")
            elif len(run) == 1:
                draw.ellipse([run[0][0] - 3, run[0][1] - 3, run[0][0] + 3, run[0][1] + 3], fill=color)
            run = []
    last = lay["panels"][-1]
    label = "Time (minutes)"
    draw.text(((WIDTH - draw.textlength(label, font=title)) / 2, last["y1"] + 26), label, fill=AXIS, font=title)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")


def verify_png(data: dict, image_path: Path) -> CheckResult:
    """Re-measure the picture: every reading must have line pixels (the panel's colour) at its place, and every
    missing reading's column must be empty. Proves the picture shows exactly this data."""
    from PIL import Image
    try:
        img = Image.open(image_path).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        return CheckResult.fail(f"cannot open the image ({exc})", where=str(image_path))
    lay = layout(data)
    if img.size != (lay["width"], lay["height"]):
        return CheckResult.fail("the image size does not match the layout", expected=(lay["width"], lay["height"]),
                                actual=img.size, where=str(image_path))
    px = img.load()
    bad, checked = [], 0
    ref = data.get("ref")
    if ref:
        rb = lay["panels"][ref["panel"]]
        ry = _y(data["panels"][ref["panel"]], rb, ref["value"])
        if not any(px[x, y] == REF_COLOR for x in range(rb["x0"] + 2, rb["x0"] + 9) for y in range(ry - 2, ry + 3)):
            bad.append("the dashed reference line is not drawn where the data says")
    for i, (p, box) in enumerate(zip(data["panels"], lay["panels"])):
        color = COLORS[i]
        for tm, v in zip(data["times"], p["values"]):
            x = _x(data, box, tm)
            ys = [y for y in range(box["y0"] - 2, box["y1"] + 3) if px[x, y] == color]
            checked += 1
            if v is None:
                if ys:
                    bad.append(f'panel {p["letter"]} minute {tm}: a missing reading has a line through it')
            else:
                want = _y(p, box, v)
                if not any(abs(y - want) <= 2 for y in ys):
                    bad.append(f'panel {p["letter"]} minute {tm}: reading {v} is not on the line (expected y={want})')
    if bad:
        return CheckResult.fail(f"{len(bad)} reading(s) do not match the picture: " + "; ".join(bad[:3]),
                                where=str(image_path))
    return CheckResult.ok(f"all {checked} readings (and gaps) in {len(data['panels'])} panels match the picture's pixels")


class VitalsGenerator(TaskGenerator):
    """Base for the vitals_* generators. Subclasses set the identity, `level_offset`, and implement `choose` and
    `compute_answer`/`make_question`. (This class lives in a module starting with `_`, so it is never registered.)"""
    visual_type = "patient vital-signs trend chart (synthetic)"
    image_ext = "png"
    level_offset = 0  # spreads the levels: with few tasks per generator, tasks still cover easy to hard
    enabled = True

    def render(self, data: dict, out_path: Path) -> None:
        render_png(data, out_path)

    def verify_render(self, data: dict, image_path: Path) -> CheckResult:
        return verify_png(data, image_path)

    def choose(self, data: dict, rng: random.Random) -> bool:
        """Pick the target panel (etc.) and add it to `data`. Return False to try another patient."""
        return True

    def generate_data(self, rng: random.Random, index: int) -> dict:
        level = (index * 3 + self.level_offset) % 10 + 1
        for _ in range(500):
            data = build_patient(rng, level)
            if self.choose(data, rng):
                return data
        raise RuntimeError(f"no patient at level {level} suits {self.name} after 500 tries")


class HardVitalsGenerator(VitalsGenerator):
    """Base for the HARD vitals generators: always levels 8-10 (more noise) on long, dense time axes (91 or 121 readings per panel), 4-5 panels, and questions
    that need counting or exact reading rather than a rough look. Still fully computed from the saved data."""
    panels = 5
    missing: float | None = None
    axis_mode: str | None = None
    grids = [(180, 2), (240, 2)]  # 91 or 121 readings per panel: dense lines

    def generate_data(self, rng: random.Random, index: int) -> dict:
        level = 8 + (index + self.level_offset) % 3
        for _ in range(2000):
            data = build_patient(rng, level, panels=self.panels, missing=self.missing, axis_mode=self.axis_mode,
                                 grids=self.grids)
            if self.choose(data, rng):
                return data
        raise RuntimeError(f"no patient at level {level} suits {self.name} after 2000 tries")
