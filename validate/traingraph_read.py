"""Check a train graph PNG against its data, and solve the question a second way.

1. Pixel check. Every train in the data is drawn where the data says, in its class
   colour (allowing for short stretches hidden under a line drawn on top), and the
   plot holds no train-coloured pixel that no train in the data explains. The
   geometry used here (plot rectangle, time span, station heights, colours) is the
   calibration recorded by the renderer; the path itself is recomputed here from
   the station calls, not taken from the renderer.
2. Second solver. Every answer is recomputed by sampling each train's position
   every 0.05 minutes and watching which lines change sides. This is a different
   method from the generator's exact crossing algebra and shares no code with it.

Limit: this does not rebuild the timetable from pixels alone; it confirms that the
image shows the data and that two independent methods agree on the answer.
"""
from __future__ import annotations

import numpy as np

STEP = 0.05
MIN_OWN = 0.80        # share of a train's path that must show its own colour
MIN_ANY = 0.995       # share that must show some train colour


def _paths(scene: dict) -> dict:
    km = [s["km"] for s in scene["stations"]]
    out = {}
    for tr in scene["trains"]:
        pts = []
        for k, a, d in tr["stops"]:
            pts.append((a, km[k]))
            if d > a:
                pts.append((d, km[k]))
        out[str(tr["num"])] = pts
    return out


def _near(m: np.ndarray) -> np.ndarray:
    out = m.copy()
    out[1:] |= m[:-1]
    out[:-1] |= m[1:]
    out[:, 1:] |= m[:, :-1]
    out[:, :-1] |= m[:, 1:]
    return out


def read(img: np.ndarray, layout: dict) -> dict:
    px0, py0, px1, py1 = layout["plot"]
    masks = {k: np.all(img == np.array(c, dtype=np.uint8), axis=2) for k, c in layout["colors"].items()}
    return {"layout": layout, "masks": masks,
            "pixels": {k: int(m[py0 - 2:py1 + 3, px0:px1 + 1].sum()) for k, m in masks.items()}}


def describe(state: dict) -> str:
    return "train-coloured pixels found in the plot: " + ", ".join(f"{k} {v}" for k, v in state["pixels"].items())


def matches_data(data: dict, state: dict) -> tuple:
    scene, lay, masks = data["scene"], state["layout"], state["masks"]
    px0, py0, px1, py1 = lay["plot"]
    t0, t1 = lay["t0"], lay["t1"]
    km = [s["km"] for s in scene["stations"]]
    ppm = (px1 - px0) / (t1 - t0)
    anym = np.zeros_like(next(iter(masks.values())))
    for m in masks.values():
        anym |= m
    own_w = {k: _near(m) for k, m in masks.items()}
    any_w = _near(anym)
    explained = np.zeros_like(anym)
    worst = (1.0, "")
    labels = {lab["text"]: lab for lab in lay["labels"] if lab["role"] == "train"}
    paths = _paths(scene)
    for tr in scene["trains"]:
        num = str(tr["num"])
        pts = paths[num]
        xs, ys = [], []
        for (ta, ka), (tb, kb) in zip(pts, pts[1:]):
            if ta >= t1:
                break
            tb2 = min(tb, t1)
            kb2 = ka + (kb - ka) * ((tb2 - ta) / (tb - ta) if tb > ta else 0)
            n = int(max(abs((tb2 - ta) * ppm), abs(kb2 - ka) / km[-1] * (py1 - py0))) + 2
            xs += list(px0 + (np.linspace(ta, tb2, n) - t0) * ppm)
            ys += list(py0 + np.linspace(ka, kb2, n) / km[-1] * (py1 - py0))
        xi = np.clip(np.rint(xs).astype(int), px0, px1)
        yi = np.rint(ys).astype(int)
        own, anyc = float(own_w[tr["kind"]][yi, xi].mean()), float(any_w[yi, xi].mean())
        if anyc < MIN_ANY:
            return False, f"train {num}: {1 - anyc:.1%} of its path has no train line in the image"
        if own < MIN_OWN:
            return False, f"train {num}: only {own:.0%} of its path shows its own colour"
        worst = min(worst, (own, num))
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                explained[np.clip(yi + dy, 0, explained.shape[0] - 1),
                          np.clip(xi + dx, 0, explained.shape[1] - 1)] = True
        lab = labels.get(num)
        x_start = px0 + (tr["stops"][0][2] - t0) * ppm
        if lab is None or abs((lab["bbox"][0] + lab["bbox"][2]) / 2 - x_start) > 1.5:
            return False, f"train {num}: its number is not printed where its line starts"
    plot = np.zeros_like(anym)
    plot[py0 - 2:py1 + 3, px0:px1 + 1] = True
    stray = int((anym & plot & ~explained).sum())
    if stray > 0:
        return False, f"{stray} train-coloured pixels in the plot belong to no train in the data"
    if len(labels) != len(scene["trains"]):
        return False, f"{len(labels)} train numbers printed for {len(scene['trains'])} trains"
    return True, (f"all {len(scene['trains'])} trains drawn along their whole path (lowest own-colour share "
                  f"{worst[0]:.0%}, train {worst[1]}), each numbered at its start, and no stray train pixels")


def _track(scene: dict):
    ts = np.arange(scene["t0"], scene["t1"] + 1e-9, STEP)
    out = {}
    for num, pts in _paths(scene).items():
        t, y = zip(*pts)
        out[num] = np.interp(ts, t, y, left=np.nan, right=np.nan)
    return ts, out


def _swaps(a, b, ts):
    """Times at which line a moves from one side of line b to the other: [(time, side before)]."""
    d = a - b
    out, last = [], 0
    for i in np.flatnonzero(~np.isnan(d)):
        s = 0 if abs(d[i]) < 1e-6 else int(np.sign(d[i]))
        if s and last and s != last:
            out.append((float(ts[i]), last))
        if s:
            last = s
    return out


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def answer(state: dict, program: str, parsed: dict, data: dict):
    scene = data["scene"]
    ts, track = _track(scene)
    info = {str(t["num"]): t for t in scene["trains"]}
    names = [s["name"] for s in scene["stations"]]
    km = [s["km"] for s in scene["stations"]]
    if program in ("tg_overtakes", "tg_meets"):
        x = info[parsed["train"]]
        ev = []
        for num, tr in info.items():
            if num == parsed["train"]:
                continue
            for when, before in _swaps(track[parsed["train"]], track[num], ts):
                ev.append((when, num, tr["dir"] == x["dir"], before * x["dir"]))
        if program == "tg_meets":
            return sum(1 for e in ev if not e[2])
        # overtaken: a same-direction train that was behind x (less far along) ends up ahead of it
        return [num for _, num, same, before in sorted(ev) if same and before > 0]
    if program == "tg_section":
        lo, hi = km[names.index(parsed["a"])], km[names.index(parsed["b"])]
        i = int(round((_minutes(parsed["time"]) - scene["t0"]) / STEP))
        return sorted((n for n, y in track.items() if not np.isnan(y[i]) and lo + 1e-6 < y[i] < hi - 1e-6), key=int)
    if program == "tg_nth":
        target, after = km[names.index(parsed["station"])], _minutes(parsed["time"])
        ev = []
        for n, y in track.items():
            hit = np.flatnonzero(np.abs(y - target) < 1e-6)
            if hit.size and ts[hit[0]] > after:
                ev.append((float(ts[hit[0]]), n))
        n_th = {"second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6}[parsed["ordinal"]]
        return sorted(ev)[n_th - 1][1]
    if program == "tg_longest":
        best = (0, "")
        for n, y in track.items():
            run = 0
            for i in range(1, len(ts)):
                still = (not np.isnan(y[i]) and abs(y[i] - y[i - 1]) < 1e-9
                         and any(abs(y[i] - v) < 1e-6 for v in km[1:-1]))
                run = run + 1 if still else 0
                best = max(best, (run, n))
        return best[1]
    raise ValueError(f"no second solver for {program}")
