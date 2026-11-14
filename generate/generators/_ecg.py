"""Shared core for the synthetic ECG rhythm-strip generators (ecg_*.py).

SYNTHETIC measuring-and-counting tasks. These are NOT clinical cases, NOT clinically validated, and nothing here is a
diagnosis: no question ever asks what a rhythm "is" or whether anything is wrong. A strip is a made-up single-lead trace
(250 samples per second, 10 seconds) drawn on the standard pink ECG grid (25 mm/s, 10 mm/mV; small box 1 mm = 0.04 s,
large box 5 mm = 0.2 s). Each beat is a sum of Gaussian bumps (P, Q, R, S, T); a "premature" beat is wider, taller, has
no P wave and an inverted T. Seeded noise and slow baseline wander are added.

DATA FIRST: the saved data holds every sample and the list of beats (sample index + type). The picture is drawn from the
samples; answers are computed from the saved beats; `verify_png` re-measures the picture (grid spacing and the trace
at sampled columns) to prove it shows exactly those samples. Pillow only; randomness only from the `rng` you are given.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from generate.base import CheckResult, TaskGenerator

FS = 250  # samples per second
DURATION = 10  # seconds
PXMM = 6  # pixels per millimetre
MM_PER_S, MM_PER_MV = 25, 10
X0, TOP, GRID_H_MM = 70, 34, 46  # strip starts 70 px from the left; grid is 46 mm tall
BASE_MM = 29  # baseline height from the top of the grid, in mm
BG, MINOR, MAJOR, INK = (255, 255, 255), (253, 224, 224), (245, 170, 170), (0, 0, 0)
WIDTH = X0 + DURATION * MM_PER_S * PXMM + 14
HEIGHT = TOP + GRID_H_MM * PXMM + 10
N = FS * DURATION
APEX_SLACK = 6  # the apex found in the noisy samples may sit this many samples from the stored beat centre


def _gauss(t: float, mu: float, amp: float, sd: float) -> float:
    z = (t - mu) / sd
    return amp * math.exp(-0.5 * z * z) if abs(z) < 6 else 0.0


# (offset seconds, amplitude relative to R amplitude A (or absolute mV for P/T), sd seconds)
def _beat_value(t_rel: float, kind: str, a: float, t_amp: float = 0.30) -> float:
    if kind == "normal":
        return (_gauss(t_rel, -0.16, 0.12, 0.025) + _gauss(t_rel, -0.03, -0.12, 0.010) + _gauss(t_rel, 0.0, a, 0.012)
                + _gauss(t_rel, 0.03, -0.25 * a, 0.012) + _gauss(t_rel, 0.26, t_amp, 0.05))
    return _gauss(t_rel, 0.0, 1.3 * a, 0.035) + _gauss(t_rel, 0.28, -0.40, 0.07)  # premature (wide, tall, inverted T)


def beat_times(rng: random.Random, rhythm: str, rate: float) -> tuple[list[int], list[str]]:
    """Sample indices and types of the beats. regular: steady with +/-1 sample jitter. irregular: random intervals around the mean (0.65x to 1.35x).
    pvc: steady, but one early beat (0.6 of the interval) followed by a longer pause (1.4 of it).
    ectopy: like pvc but 3-5 early beats (never adjacent), each followed by a longer pause."""
    rr = FS * 60.0 / rate
    t = int(rng.uniform(0.45, 0.85) * FS)
    times, types = [t], ["normal"]
    pvc_at = None
    pvc_set: set[int] = set()
    want_pvc = rng.randint(3, 5) if rhythm == "ectopy" else 0
    while True:
        if rhythm == "irregular":
            step = rng.uniform(0.65, 1.35) * rr
        elif rhythm == "ectopy":
            if len(pvc_set) < want_pvc and len(times) >= 3 and (len(times) - 1) not in pvc_set \
                    and (len(times) - 2) not in pvc_set and rng.random() < 0.3:
                pvc_set.add(len(times))
                step = 0.6 * rr
            elif (len(times) - 1) in pvc_set:
                step = 1.4 * rr
            else:
                step = rr + rng.choice([-1, 0, 1])
        elif rhythm == "pvc":
            if pvc_at is None and len(times) >= 3 and rng.random() < 0.35:
                pvc_at, step = len(times), 0.6 * rr
            elif pvc_at is not None and len(times) == pvc_at + 1:
                step = 1.4 * rr
            else:
                step = rr + rng.choice([-1, 0, 1])
        else:
            step = rr + rng.choice([-1, 0, 1])
        t = int(round(times[-1] + step))
        if t > N - int(0.5 * FS):
            break
        times.append(t)
        types.append("pvc" if ((pvc_at is not None and len(times) - 1 == pvc_at) or (len(times) - 1) in pvc_set) else "normal")
    return times, types


def build_strip(rng: random.Random, rhythm: str, rate: float) -> dict:
    a = round(rng.uniform(1.0, 1.5), 2)
    noise = round(rng.uniform(0.05, 0.09), 3)  # strong noise (muscle-tremor-like)
    wander = round(rng.uniform(0.15, 0.30), 3)  # big slow baseline drift
    t_amp = round(rng.uniform(0.30, 0.50), 2)
    phase = rng.uniform(0, 2 * math.pi)
    times, types = beat_times(rng, rhythm, rate)
    samples = []
    for n in range(N):
        t = n / FS
        v = wander * math.sin(2 * math.pi * 0.25 * t + phase) + rng.gauss(0, 1) * noise
        for bt, ty in zip(times, types):
            d = t - bt / FS
            if -0.3 < d < 0.5:
                v += _beat_value(d, ty, a, t_amp)
        samples.append(round(v, 3))
    return {"fs": FS, "duration": DURATION, "rhythm": rhythm, "amp": a, "noise": noise, "wander": wander, "t_amp": t_amp,
            "beats": [{"i": i, "type": ty} for i, ty in zip(times, types)], "samples": samples}


def find_beats(samples: list[float], a: float) -> list[int]:
    """INDEPENDENT beat finder (used by answers' cross-checks): local maxima above 0.6 mV at least 0.25 s apart."""
    out: list[int] = []
    thr, gap = 0.6, int(0.25 * FS)
    for n in range(1, len(samples) - 1):
        if samples[n] >= thr and samples[n] >= samples[n - 1] and samples[n] > samples[n + 1]:
            if out and n - out[-1] < gap:
                if samples[n] > samples[out[-1]]:
                    out[-1] = n
            else:
                out.append(n)
    return out


# ---- drawing --------------------------------------------------------------------------------------------------------
def _xy(n: int, v: float) -> tuple[int, int]:
    return (int(round(X0 + n / FS * MM_PER_S * PXMM)), int(round(TOP + BASE_MM * PXMM - v * MM_PER_MV * PXMM)))


def _font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def render_png(d: dict, out_path: Path) -> None:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    gw, gh = DURATION * MM_PER_S * PXMM, GRID_H_MM * PXMM
    for mm in range(0, DURATION * MM_PER_S + 1):  # vertical lines: minor every mm, major every 5 mm
        x = X0 + mm * PXMM
        draw.line([(x, TOP), (x, TOP + gh)], fill=MAJOR if mm % 5 == 0 else MINOR, width=1)
    for mm in range(0, GRID_H_MM + 1):
        y = TOP + mm * PXMM
        draw.line([(X0, y), (X0 + gw, y)], fill=MAJOR if mm % 5 == 0 else MINOR, width=1)
    small = _font(15)
    draw.text((X0, 8), "Lead II    25 mm/s    10 mm/mV    synthetic strip", fill=INK, font=small)
    # calibration pulse (1 mV tall, 0.2 s wide) in the left margin
    cx, by = 14, TOP + BASE_MM * PXMM
    draw.line([(cx, by), (cx + 6, by), (cx + 6, by - MM_PER_MV * PXMM), (cx + 6 + 5 * PXMM, by - MM_PER_MV * PXMM),
               (cx + 6 + 5 * PXMM, by), (X0 - 4, by)], fill=INK, width=2)
    pts = [_xy(n, v) for n, v in enumerate(d["samples"])]
    draw.line(pts, fill=INK, width=2, joint="curve")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")


def verify_png(d: dict, image_path: Path) -> CheckResult:
    """The grid must be drawn at the stated scale (major lines every 30 px) and, at every 6th sample, the picture must
    have ink at the sample's position; every beat's apex is checked too. Proves the picture is the data."""
    from PIL import Image
    try:
        img = Image.open(image_path).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        return CheckResult.fail(f"cannot open the image ({exc})", where=str(image_path))
    if img.size != (WIDTH, HEIGHT):
        return CheckResult.fail("the image size does not match the layout", expected=(WIDTH, HEIGHT), actual=img.size,
                                where=str(image_path))
    px = img.load()
    bad = [k for k in range(0, DURATION * 5 + 1) if px[X0 + k * 5 * PXMM, TOP + 1] != MAJOR]
    if bad:
        return CheckResult.fail(f"the grid is not drawn at 5 mm spacing (first bad line {bad[0]})", where=str(image_path))
    missing, checked = [], 0
    for n in sorted(set(range(0, N, 6)) | {b["i"] for b in d["beats"]}):  # every 6th sample and every beat apex
        x, y = _xy(n, d["samples"][n])
        checked += 1
        if not any(px[xx, yy] == INK for xx in (x - 1, x, x + 1) for yy in range(y - 2, y + 3)
                   if 0 <= xx < WIDTH and 0 <= yy < HEIGHT):
            missing.append(n)
    if missing:
        return CheckResult.fail(f"{len(missing)} sample position(s) have no trace in the picture (first: sample {missing[0]})",
                                where=str(image_path))
    return CheckResult.ok(f"grid scale and {checked} trace positions match the picture")


class EcgGenerator(TaskGenerator):
    """Base for ecg_* generators (module starts with `_`, so never registered itself)."""
    visual_type = "ECG rhythm strip (synthetic)"
    image_ext = "png"
    enabled = True
    rhythm = "regular"
    rate_range = (60, 100)

    def render(self, data: dict, out_path: Path) -> None:
        render_png(data, out_path)

    def verify_render(self, data: dict, image_path: Path) -> CheckResult:
        return verify_png(data, image_path)

    def choose(self, data: dict, rng: random.Random) -> bool:
        return True

    def generate_data(self, rng: random.Random, index: int) -> dict:
        for _ in range(500):
            data = build_strip(rng, self.rhythm, rng.randint(*self.rate_range))
            found = find_beats(data["samples"], data["amp"])
            if len(found) != len(data["beats"]) or any(abs(p - b["i"]) > APEX_SLACK for p, b in zip(found, data["beats"])):
                continue  # the independent finder must find every stored beat, within a few samples
            if self.choose(data, rng):
                return data
        raise RuntimeError(f"no strip suits {self.name} after 500 tries")
