"""Question programs over a train graph. Each one asks for a different chain of reading steps."""
from __future__ import annotations

from ...core import fingerprints as fp
from ...core.program import Program
from ...core.seed import Reject, pick, randint
from . import model as m

TAIL = "\n\nEnd your reply with one line in exactly this form:\nFINAL ANSWER: "
ORDINAL = {2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth"}


def need(st: dict, kind: str) -> float:
    """Minutes a train of this kind needs, with waits, to finish its run on the chart."""
    return (len(st["names"]) - 1) * 9.5 / m.SPEED[kind] * 1.35 + 15


def intro(scene: dict) -> str:
    a, b = scene["stations"][0]["name"], scene["stations"][-1]["name"]
    return (f"This is a graphic timetable (train graph) for the double-track railway line between {a} and {b}. "
            "Time runs from left to right: there is a labelled grid line every 30 minutes and lighter lines every "
            "10 and 5 minutes. Each horizontal line is a station, and the stations are spaced by distance. Each "
            f"coloured line is one train. A line that slopes down to the right is a train running from {a} towards "
            f"{b}; a line that slopes up to the right runs the other way. A horizontal stretch on a station line is "
            "the train standing at that station. A train's number is printed where its line starts: above the top "
            f"station line for trains starting at {a}, below the bottom station line for trains starting at {b}. "
            "The legend gives the colour of each kind of train. A line that ends at a station in the middle of the "
            "chart is a train that terminates there, and a line that reaches the right-hand edge is a train still "
            "running when the chart ends.\n\n")


class TrainGraph(Program):
    family = "traingraph"

    def target(self, rs, st: dict, level: int) -> dict:
        return {}

    def structure(self, rs, level, variant):
        st = m.make_structure(rs, level)
        st["conventions"] = self.target(rs, st, level)
        return st

    def fill(self, rd, st, level):
        stations, trains = m.make_timetable(rd, st)
        scene = {"style": st["style"], "t0": st["t0"], "t1": st["t1"], "stations": stations, "trains": trains,
                 "title": (f"{st['names'][0]} - {st['names'][-1]}   graphic timetable   line {st['line_ref']}   "
                           f"Mon-Fri {m.hhmm(st['t0'])}-{m.hhmm(st['t1'])}"),
                 "conventions": dict(st["conventions"]), "record_extra": {}}
        m.label_tiers(scene)
        held = {}
        for tr in trains:                    # two trains of the same kind and direction standing at one station at
            for k, a, d in tr["stops"]:      # once could not be told apart where their lines overlap: redraw
                if d > a and a < st["t1"]:
                    held.setdefault((k, tr["kind"], tr["dir"]), []).append((a, d))
        for spans in held.values():
            spans.sort()
            if any(b[0] < a[1] + 1.5 for a, b in zip(spans, spans[1:])):
                raise Reject("two trains stand at the same station at the same time")
        return scene

    @staticmethod
    def _slot(rs, st, kinds):
        for kind in kinds:
            pool = [s for s in st["slots"] if s["kind"] == kind and s["ends"] is None
                    and s["nominal"] <= st["t1"] - need(st, kind)]
            if pool:
                return pick(rs, pool)
        raise Reject("no train of the wanted kind finishes its run on the chart")

    @staticmethod
    def _whole(scene, x):
        if m.span(x)[1] > scene["t1"] - 3:
            raise Reject("the train does not finish its run on the chart")


class TgOvertakes(TrainGraph):
    """Follow one slow train and name, in order, every faster train that passes it while it stands."""
    name = "tg_overtakes"
    answer_type = "sequence"
    answer_format = "sequence"
    answer_space = 5000

    def target(self, rs, st, level):
        return {"train": self._slot(rs, st, ("FR", "RE"))["num"]}

    def question(self, scene):
        x = scene["conventions"]["train"]
        return (intro(scene) + "A train is overtaken when it stands at a station while a train running in the same "
                f"direction passes it.\n\nQuestion: which trains overtake train {x}? List their numbers in the "
                "order in which the overtakes happen." + TAIL + "<number>, <number>, ...")

    def _events(self, scene):
        x = m.by_num(scene, scene["conventions"]["train"])
        return x, [(t, y, s) for t, y, s in m.passes(scene, x, True) if m.span(y)[0] > m.span(x)[0]]

    def solve(self, scene):
        return [str(y["num"]) for _, y, _ in self._events(scene)[1]]

    def check(self, scene, answer):
        x, ev = self._events(scene)
        self._whole(scene, x)
        if len(answer) < min(scene["level"], 3):
            raise Reject("too few overtakes")
        if any(s is None or s in (x["stops"][0][0], x["stops"][-1][0]) for _, _, s in ev):
            raise Reject("an overtake does not happen at an intermediate station")

    def fingerprints(self, scene, answer):
        x, ev = self._events(scene)
        out = []
        by_dep = [str(y["num"]) for _, y, _ in sorted(ev, key=lambda e: m.span(e[1])[0])]
        fp.add_unique(out, answer, "ordered_by_departure", "listed the right trains in the order they start, not "
                      "the order they pass", by_dep)
        standing = {k: (a, d) for k, a, d in x["stops"][1:-1] if d > a}
        opp = [(t, y) for t, y, s in m.passes(scene, x, False) if s in standing]
        mixed = [str(y["num"]) for t, y in sorted([(t, y) for t, y, _ in ev] + opp, key=lambda e: e[0])]
        fp.add_unique(out, answer, "counted_opposite_trains", "also listed trains running the other way that pass "
                      "while it stands", mixed)
        x0, x1 = m.span(x)
        later = [y for y in scene["trains"] if y["dir"] == x["dir"] and y is not x and x0 < m.span(y)[0] < x1]
        fp.add_unique(out, answer, "every_train_starting_behind", "listed every train that starts behind it before "
                      "it finishes", [str(y["num"]) for y in sorted(later, key=lambda y: m.span(y)[0])])
        first = ev[0][2]
        fp.add_unique(out, answer, "first_stop_only", "stopped after the first station where it is overtaken",
                      [str(y["num"]) for _, y, s in ev if s == first])
        out.append(fp.entry("slip:missed_last", fp.SINGLE_SLIP, "missed the last overtake", answer[:-1]))
        out.append(fp.entry("slip:missed_first", fp.SINGLE_SLIP, "missed the first overtake", answer[1:]))
        return [e for e in out if e["answer"]]


class TgMeets(TrainGraph):
    """Count every opposite-direction line that crosses one train's line."""
    name = "tg_meets"
    answer_type = "integer"
    answer_format = "int"
    answer_space = 30

    def target(self, rs, st, level):
        return {"train": self._slot(rs, st, ("FR", "RE") if level >= 3 else ("RE", "IC"))["num"]}

    def question(self, scene):
        x = scene["conventions"]["train"]
        return (intro(scene) + f"Question: how many trains running in the opposite direction does train {x} meet "
                f"between its first and its last station? Count every train whose line crosses the line of train "
                f"{x}, including trains it meets while standing at a station." + TAIL + "<integer>")

    def solve(self, scene):
        return len(m.passes(scene, m.by_num(scene, scene["conventions"]["train"]), False))

    def check(self, scene, answer):
        x = m.by_num(scene, scene["conventions"]["train"])
        self._whole(scene, x)
        if answer < (4 if scene["level"] <= 2 else 8):
            raise Reject("too few meets")
        km = m.km_list(scene)
        px, (x0, x1) = m.path(x, km), m.span(x)
        crossed = {y["num"]: t for t, y, _ in m.passes(scene, x, False)}
        for y in scene["trains"]:
            if y["dir"] == x["dir"]:
                continue
            y0, y1 = m.span(y)
            if y["num"] in crossed:
                t = crossed[y["num"]]
                if min(t - x0, x1 - t, t - y0, y1 - t, scene["t1"] - t) < 2:
                    raise Reject("a meet happens too close to the end of a line to call")
                continue
            lo, hi = max(x0, y0), min(x1, y1, scene["t1"])
            ends = [(x0, y0, x["stops"][0][0], y["stops"][-1][0]), (x1, y1, x["stops"][-1][0], y["stops"][0][0])]
            if hi > lo:
                py = m.path(y, km)
                if min(abs(m.pos(px, t) - m.pos(py, t)) for t in (lo, hi)) < 2.5:
                    raise Reject("two lines nearly touch without crossing")
            elif any(abs(a - b) < 2.5 for a, b, sa, sb in ends if sa == sb) or lo - hi < 2.5 and (
                    x["stops"][0][0] == y["stops"][-1][0] or x["stops"][-1][0] == y["stops"][0][0]):
                raise Reject("one line ends just where the other starts")

    def fingerprints(self, scene, answer):
        x = m.by_num(scene, scene["conventions"]["train"])
        opp, same = m.passes(scene, x, False), m.passes(scene, x, True)
        out = []
        fp.add_unique(out, answer, "all_crossing_lines", "counted every line that crosses it, including trains "
                      "running the same way", answer + len(same))
        fp.add_unique(out, answer, "open_line_only", "left out the trains it meets while standing at a station",
                      sum(1 for _, _, s in opp if s is None))
        x0, x1 = m.span(x)
        others = [y for y in scene["trains"] if y["dir"] != x["dir"]]
        mean_run = sum(m.span(y)[1] - m.span(y)[0] for y in others) / len(others)
        fp.add_unique(out, answer, "start_time_estimate", "estimated from start times instead of following the "
                      "lines", sum(1 for y in others if x0 - mean_run < m.span(y)[0] < x1))
        out.append(fp.entry("slip:one_missed", fp.SINGLE_SLIP, "missed one crossing", answer - 1))
        out.append(fp.entry("slip:one_extra", fp.SINGLE_SLIP, "counted one crossing twice", answer + 1))
        return out


class TgSection(TrainGraph):
    """Cut the chart at one time and one stretch of line, then trace each line found there to its number."""
    name = "tg_section"
    answer_type = "sequence"
    answer_format = "name_set"
    answer_space = 5000

    def target(self, rs, st, level):
        width = 2 + level // 2                      # the stretch spans this many neighbouring sections
        k = randint(rs, 0, len(st["names"]) - 1 - width)
        t = 10 * randint(rs, (st["t0"] + 50) // 10, (st["t1"] - 40) // 10)
        return {"section": k, "width": width, "a": st["names"][k], "b": st["names"][k + width], "time": t}

    def question(self, scene):
        c = scene["conventions"]
        return (intro(scene) + f"Question: which trains are between {c['a']} and {c['b']} at "
                f"{m.hhmm(c['time'])}, in either direction? Count trains running on the line and trains standing "
                f"at a station in between, but not a train that is standing at {c['a']} or at {c['b']} at that "
                "moment. List the train numbers in ascending order." + TAIL + "<number>, <number>, ...")

    @staticmethod
    def _inside(scene, k, t, direction=None, standing=False):
        km, out, hi = m.km_list(scene), [], k + scene["conventions"]["width"]
        if k < 0 or hi > len(km) - 1:
            return []
        for tr in scene["trains"]:
            p = m.pos(m.path(tr, km), t)
            if p is None or (direction is not None and tr["dir"] != direction):
                continue
            if km[k] < p < km[hi] or (standing and p in (km[k], km[hi])):
                out.append(tr["num"])
        return [str(n) for n in sorted(out)]

    def solve(self, scene):
        c = scene["conventions"]
        return self._inside(scene, c["section"], c["time"])

    def check(self, scene, answer):
        c = scene["conventions"]
        if not (2 if scene["level"] <= 2 else 3) <= len(answer) <= 7:
            raise Reject("too few or too many trains in the stretch")
        if any(self._inside(scene, c["section"], c["time"] + dt) != answer for dt in (-1.5, 1.5)):
            raise Reject("a train enters or leaves the section too close to the asked time")

    def fingerprints(self, scene, answer):
        c, out = scene["conventions"], []
        k, t = c["section"], c["time"]
        fp.add_unique(out, answer, "one_direction_down", "listed only trains running down the chart",
                      self._inside(scene, k, t, 1))
        fp.add_unique(out, answer, "one_direction_up", "listed only trains running up the chart",
                      self._inside(scene, k, t, -1))
        fp.add_unique(out, answer, "counted_standing_trains", "also listed trains standing at the two stations",
                      self._inside(scene, k, t, standing=True))
        fp.add_unique(out, answer, "ten_minutes_early", "read the grid line ten minutes earlier",
                      self._inside(scene, k, t - 10))
        fp.add_unique(out, answer, "ten_minutes_late", "read the grid line ten minutes later",
                      self._inside(scene, k, t + 10))
        fp.add_unique(out, answer, "one_station_up", "started one station too high on the chart",
                      self._inside(scene, k - 1, t))
        fp.add_unique(out, answer, "one_station_down", "started one station too low on the chart",
                      self._inside(scene, k + 1, t))
        return [e for e in out if e["answer"]]


class TgNth(TrainGraph):
    """Order the arrivals at one station after a given time, across both directions."""
    name = "tg_nth"
    answer_type = "label"
    answer_format = "label"
    answer_space = 48

    def target(self, rs, st, level):
        s = randint(rs, 1, len(st["names"]) - 2)
        t = 10 * randint(rs, (st["t0"] + 30) // 10, (st["t1"] - 80) // 10)
        return {"s": s, "station": st["names"][s], "time": t, "n": 3 + level // 2}

    def question(self, scene):
        c = scene["conventions"]
        return (intro(scene) + "A train arrives at a station at the moment its line first reaches that station's "
                "line; a train that runs through without stopping arrives when it passes.\n\nQuestion: counting "
                f"trains in both directions, which train is the {ORDINAL[c['n']]} to arrive at {c['station']} "
                f"after {m.hhmm(c['time'])}?" + TAIL + "<number>")

    @staticmethod
    def _events(scene, s, t, direction=None, by_departure=False):
        out = []
        for tr in scene["trains"]:
            for k, a, d in tr["stops"]:
                when = d if by_departure else a
                if k == s and t < when <= scene["t1"] and (direction is None or tr["dir"] == direction):
                    out.append((when, tr["num"]))
        return sorted(out)

    def solve(self, scene):
        c = scene["conventions"]
        ev = self._events(scene, c["s"], c["time"])
        if len(ev) < c["n"] + 1:
            raise Reject("not enough arrivals after the asked time")
        return str(ev[c["n"] - 1][1])

    def check(self, scene, answer):
        c = scene["conventions"]
        ev = self._events(scene, c["s"], c["time"])[:c["n"] + 1]
        before = [a for tr in scene["trains"] for k, a, _ in tr["stops"] if k == c["s"] and a <= c["time"]]
        if ev[-1][0] > scene["t1"] - 2 or ev[0][0] - c["time"] < 1.5 or (before and c["time"] - max(before) < 1.5):
            raise Reject("an arrival is too close to the asked time or to the edge")
        if any(b[0] - a[0] < 1.0 for a, b in zip(ev, ev[1:])):
            raise Reject("two arrivals are too close together to order")

    def fingerprints(self, scene, answer):
        c, out = scene["conventions"], []
        s, t, n = c["s"], c["time"], c["n"]

        def nth(ev, i):
            return str(ev[i - 1][1]) if 0 < i <= len(ev) else None

        for d, fid in ((1, "down_trains_only"), (-1, "up_trains_only")):
            got = nth(self._events(scene, s, t, d), n)
            if got:
                fp.add_unique(out, answer, fid, "counted trains in one direction only", got)
        got = nth(self._events(scene, s, t, by_departure=True), n)
        if got:
            fp.add_unique(out, answer, "ordered_by_departure", "ordered the trains by when they leave the station",
                          got)
        standing = sorted((a, tr["num"]) for tr in scene["trains"] for k, a, d in tr["stops"] if k == s and a <= t < d)
        got = nth(standing + self._events(scene, s, t), n)
        if got:
            fp.add_unique(out, answer, "counted_standing_trains", "counted trains already standing at the station",
                          got)
        got = nth(self._events(scene, s, t - 10), n)
        if got:
            fp.add_unique(out, answer, "ten_minutes_early", "started counting ten minutes too early", got)
        ev = self._events(scene, s, t)
        out.append(fp.entry("slip:one_early", fp.SINGLE_SLIP, "stopped one train too soon", nth(ev, n - 1)))
        out.append(fp.entry("slip:one_late", fp.SINGLE_SLIP, "counted one train too many", nth(ev, n + 1)))
        return [e for e in out if e["answer"]]


class TgLongest(TrainGraph):
    """Find the longest horizontal stretch anywhere on the chart and trace it back to a train number."""
    name = "tg_longest"
    answer_type = "label"
    answer_format = "label"
    answer_space = 48

    def question(self, scene):
        return (intro(scene) + "Question: which train makes the longest single stop at a station on this chart? "
                "A stop is one unbroken horizontal stretch of a train's line." + TAIL + "<number>")

    @staticmethod
    def _stops(scene):
        out = []
        for tr in scene["trains"]:
            for k, a, d in tr["stops"][1:-1]:
                if d > a and a < scene["t1"]:
                    out.append((min(d, scene["t1"]) - a, tr, d <= scene["t1"] - 2))
        return sorted(out, key=lambda s: -s[0])

    def solve(self, scene):
        stops = self._stops(scene)
        if not stops:
            raise Reject("no train stands anywhere")
        return str(stops[0][1]["num"])

    def check(self, scene, answer):
        stops = self._stops(scene)
        if len(stops) < 2 or not stops[0][2] or stops[0][0] < 6 or stops[0][0] - stops[1][0] < 2:
            raise Reject("the longest stop is not clearly the longest")

    def fingerprints(self, scene, answer):
        stops, out, total = self._stops(scene), [], {}
        fp.add_unique(out, answer, "second_longest", "picked the second longest stop", str(stops[1][1]["num"]))
        for length, tr, _ in stops:
            total[tr["num"]] = total.get(tr["num"], 0.0) + length
        fp.add_unique(out, answer, "longest_total_standing", "picked the train that stands longest in total",
                      str(max(total, key=total.get)))
        passenger = [s for s in stops if s[1]["kind"] != "FR"]
        if passenger:
            fp.add_unique(out, answer, "passenger_trains_only", "looked at passenger trains only",
                          str(passenger[0][1]["num"]))
        return out


PROGRAMS = {p.name: p for p in (TgOvertakes(), TgMeets(), TgSection(), TgNth(), TgLongest())}
