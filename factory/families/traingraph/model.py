"""The timetable behind a train graph, and exact questions about it.

A scene is a list of stations (name, distance) and trains (number, kind,
direction, and an arrival and departure time at every station it reaches).
Everything the image shows is in that list; the image is drawn from it and every
answer is computed from it.
"""
from __future__ import annotations

from ...core.seed import Reject, pick, randint, shuffled

HEADWAY = 3.0                                   # minutes between two trains on the same track
SPEED = {"IC": 2.0, "RE": 1.3, "FR": 0.9}       # km per minute: 120, 78 and 54 km/h
BASE = {"IC": 1200, "RE": 3700, "FR": 6100}     # train number series
CLASS_NAME = {"IC": "IC express", "RE": "RE regional", "FR": "freight"}
_P6 = [("IC", 4), ("RE", 14), ("FR", 24), ("IC", 34), ("RE", 44), ("FR", 54)]
LEVELS = {
    1: {"hours": 3, "stations": 6, "pattern": [("IC", 6), ("RE", 26), ("FR", 46)], "short": 0},
    2: {"hours": 4, "stations": 7, "pattern": [("IC", 5), ("RE", 20), ("FR", 35), ("RE", 50)], "short": 0},
    3: {"hours": 4, "stations": 8, "pattern": _P6[:5], "short": 1},
    4: {"hours": 4, "stations": 9, "pattern": _P6, "short": 2},
    5: {"hours": 4, "stations": 10, "pattern": _P6, "short": 3},
}
LINES = [
    ["Marlow Central", "Aldgrove", "Fenwick Jn", "Calder", "Oakstow", "Kesmere", "Brixley", "Thornholt",
     "Wexford Rd", "Eastmere"],
    ["Northgate", "Hadley Vale", "Stonebridge", "Rushton", "Elmfield Jn", "Carrow", "Wyeford", "Linton Moor",
     "Ashby Halt", "Port Halden"],
    ["Kingsbridge", "Tolworth", "Milbury", "Sandham Jn", "Overley", "Castleton", "Fairwater", "Dunmore",
     "Penhallow", "Aldermoor"],
]
STYLES = ["paper", "cream", "dark"]
PX0, PX1, PY0, PY1 = 176, 1500, 132, 930        # plot area in image pixels
LABEL_GAP = 46                                  # two train numbers on one tier are at least this far apart


def make_structure(rs, level: int) -> dict:
    """Everything that does not change between a scene and its twin."""
    cfg = LEVELS[level]
    pool = pick(rs, LINES)
    n = cfg["stations"]
    mid = sorted(shuffled(rs, list(range(1, 9)))[:n - 2])
    names = [pool[0]] + [pool[i] for i in mid] + [pool[9]]
    majors = sorted(shuffled(rs, list(range(1, n - 1)))[:2])
    t0 = 60 * pick(rs, [5, 6, 7, 13, 14, 15, 16])
    slots = []
    for d in (1, -1):
        off = 0 if d == 1 else randint(rs, 3, 7)
        for h in range(cfg["hours"]):
            for kind, minute in cfg["pattern"]:
                slots.append({"kind": kind, "dir": d, "nominal": t0 + 60 * h + minute + off, "ends": None})
    count = {}
    for s in sorted(slots, key=lambda s: s["nominal"]):
        i = count.get((s["kind"], s["dir"]), 0)
        count[(s["kind"], s["dir"])] = i + 1
        s["num"] = BASE[s["kind"]] + 2 * i + (2 if s["dir"] == 1 else 1)
    for s in shuffled(rs, [s for s in slots if s["kind"] == "RE"])[:cfg["short"]]:
        s["ends"] = pick(rs, majors)             # this regional train terminates at a main station
    return {"names": names, "majors": majors, "t0": t0, "t1": t0 + 60 * cfg["hours"], "slots": slots,
            "style": pick(rs, STYLES), "line_ref": f"{randint(rs, 2, 9)}{randint(rs, 10, 99)}"}


def _run(slot: dict, t_dep: float, km: list, majors: list, placed: list) -> dict:
    """Run one train down the line, holding it at stations so faster trains ahead of it keep their headway."""
    order = list(range(len(km))) if slot["dir"] == 1 else list(range(len(km) - 1, -1, -1))
    if slot["ends"] is not None:
        order = order[:order.index(slot["ends"]) + 1]
    kind, t, at, e = slot["kind"], t_dep, {}, 1e-6
    for i, k in enumerate(order):
        arr, last = t, i == len(order) - 1
        dwell = 0.0 if i == 0 or last else 1.0 if kind == "RE" else 2.0 if kind == "IC" and k in majors else 0.0
        dep = arr + dwell
        if not last:
            nk = order[i + 1]
            rt = abs(km[nk] - km[k]) / SPEED[kind]
            changed = True
            while changed:
                changed = False
                for f in placed:
                    if f["dir"] != slot["dir"] or k not in f["at"] or nk not in f["at"]:
                        continue
                    fd, fa = f["at"][k][1], f["at"][nk][0]
                    ahead = fd <= dep - HEADWAY + e and fa <= dep + rt - HEADWAY + e
                    behind = fd >= dep + HEADWAY - e and fa >= dep + rt + HEADWAY - e
                    if not (ahead or behind):
                        dep, changed = max(fd + HEADWAY, dep + 0.5), True
            t = dep + rt
        at[k] = (arr, dep)
    first = order[0]
    at[first] = (at[first][1], at[first][1])     # a train starts when it leaves its first station
    return {"num": slot["num"], "kind": kind, "dir": slot["dir"], "at": at, "order": order}


def make_timetable(rd, st: dict) -> tuple:
    """Return (stations, trains) for one scene."""
    n = len(st["names"])
    km = [0.0]
    for _ in range(n - 1):
        km.append(round(km[-1] + float(rd.uniform(6.5, 12.5)), 1))
    placed = []
    for kind in ("IC", "RE", "FR"):
        for s in sorted((s for s in st["slots"] if s["kind"] == kind), key=lambda s: (s["nominal"], s["dir"])):
            placed.append(_run(s, float(s["nominal"] + randint(rd, -1, 1)), km, st["majors"], placed))
    stations = [{"name": st["names"][i], "km": km[i], "major": i in st["majors"] or i in (0, n - 1)}
                for i in range(n)]
    trains = [{"num": p["num"], "kind": p["kind"], "dir": p["dir"],
               "stops": [[k, round(p["at"][k][0], 2), round(p["at"][k][1], 2)] for k in p["order"]]}
              for p in sorted(placed, key=lambda p: p["num"])
              if p["at"][p["order"][0]][1] <= st["t1"] - 8]     # a train starting in the last minutes is left off
    return stations, trains


# ---- geometry of the paths ---------------------------------------------------------------------------------------
def path(train: dict, km: list) -> list:
    """Breakpoints [(time, km)] of a train's line."""
    pts = []
    for k, a, d in train["stops"]:
        pts.append((a, km[k]))
        if d > a:
            pts.append((d, km[k]))
    return pts


def pos(pts: list, t: float):
    if t < pts[0][0] or t > pts[-1][0]:
        return None
    for (ta, ka), (tb, kb) in zip(pts, pts[1:]):
        if ta <= t <= tb:
            return ka if tb == ta else ka + (kb - ka) * (t - ta) / (tb - ta)
    return pts[-1][1]


def crossings(pa: list, pb: list) -> list:
    """Times at which two lines cross. A stretch where both stand at the same station counts once."""
    lo, hi = max(pa[0][0], pb[0][0]), min(pa[-1][0], pb[-1][0])
    if hi <= lo:
        return []
    ts = sorted({t for t, _ in pa + pb if lo <= t <= hi} | {lo, hi})
    out, last_sign, last_t, zero_t = [], 0, None, None
    for t in ts:
        d = pos(pa, t) - pos(pb, t)
        sign = 0 if abs(d) < 1e-9 else (1 if d > 0 else -1)
        if sign == 0:
            zero_t = t if zero_t is None else zero_t
        else:
            if last_sign and sign != last_sign:
                if zero_t is not None:
                    out.append(zero_t)
                else:
                    da = pos(pa, last_t) - pos(pb, last_t)
                    out.append(last_t + (t - last_t) * da / (da - d))
            last_sign, zero_t = sign, None
        last_t = t
    return out


def km_list(scene: dict) -> list:
    return [s["km"] for s in scene["stations"]]


def by_num(scene: dict, num) -> dict:
    return next(t for t in scene["trains"] if t["num"] == int(num))


def span(train: dict) -> tuple:
    return train["stops"][0][2], train["stops"][-1][1]


def station_at(scene: dict, km_value: float):
    """Index of the station at this distance, or None on the open line."""
    for i, s in enumerate(scene["stations"]):
        if abs(s["km"] - km_value) < 1e-6:
            return i
    return None


def passes(scene: dict, x: dict, same_direction: bool) -> list:
    """Trains whose line crosses the line of x on the chart: [(time, train, station index or None)]."""
    km, px, out = km_list(scene), None, []
    px = path(x, km)
    for y in scene["trains"]:
        if y is x or (y["dir"] == x["dir"]) != same_direction:
            continue
        for t in crossings(px, path(y, km)):
            if t <= scene["t1"]:
                out.append((t, y, station_at(scene, pos(px, t))))
    return sorted(out, key=lambda p: p[0])


def label_tiers(scene: dict) -> dict:
    """Which of the two label rows each train number goes on; Reject if three would collide."""
    ppm = (PX1 - PX0) / (scene["t1"] - scene["t0"])
    tiers = {}
    for d in (1, -1):
        last = [-999.0, -999.0]
        for tr in sorted((t for t in scene["trains"] if t["dir"] == d), key=lambda t: span(t)[0]):
            x = PX0 + (span(tr)[0] - scene["t0"]) * ppm
            tier = 0 if x - last[0] >= LABEL_GAP else 1 if x - last[1] >= LABEL_GAP else None
            if tier is None:
                raise Reject("three train numbers would collide at the origin station")
            last[tier] = x
            tiers[tr["num"]] = tier
    return tiers


def hhmm(minutes: int) -> str:
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"
