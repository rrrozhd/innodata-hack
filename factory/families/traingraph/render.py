"""Render a train graph. The image is a pure function of the scene."""
from __future__ import annotations

from ...core.canvas import Canvas, frozen, rgb
from . import model as m

W, H = 1536, 1024
FONT = 14.5      # smallest text: 7 px capitals after a 0.667x downscale
LINE_PX = 2      # train lines are drawn without antialiasing so every pixel has the exact class colour
THEMES = {
    "paper": {"bg": "#ffffff", "g5": "#eceef0", "g10": "#d3d6da", "g30": "#9aa0a6", "row": "#6b7075", "ink": "#000000",
              "IC": "#c62828", "RE": "#1e3a8a", "FR": "#1b7f3b"},
    "cream": {"bg": "#fbf6e9", "g5": "#efe8d6", "g10": "#ddd3ba", "g30": "#a89f88", "row": "#6e6654", "ink": "#1a1a1a",
              "IC": "#b3261e", "RE": "#1f2a6b", "FR": "#2b6a2f"},
    "dark": {"bg": "#0f1720", "g5": "#18222d", "g10": "#24303d", "g30": "#4a5868", "row": "#7b8794", "ink": "#e8edf2",
             "IC": "#ff6b5e", "RE": "#5cc8ff", "FR": "#7bd88f"},
}
DRAW_ORDER = ("FR", "RE", "IC")     # express lines are drawn last, so they stay whole at crossings


def render(scene: dict):
    """Return (png_bytes, layout)."""
    th = THEMES[scene["style"]]
    t0, t1 = scene["t0"], scene["t1"]
    km = m.km_list(scene)
    ppm = (m.PX1 - m.PX0) / (t1 - t0)
    tiers = m.label_tiers(scene)
    labels = []

    def X(t):
        return m.PX0 + (t - t0) * ppm

    def Y(k):
        return m.PY0 + k / km[-1] * (m.PY1 - m.PY0)

    with frozen():
        cv = Canvas(W, H, th["bg"])

        def put(x, y, s, role, kind, size, color, ha="left"):
            art = cv.text(x, y, s, kind=kind, size_px=size, color=color, ha=ha, va="center")
            labels.append({"text": s, "role": role, "font_px": size, "fg": rgb(color), "bg": rgb(th["bg"]), "_art": art})

        for minute in range(t0, t1 + 1, 5):
            shade = th["g30"] if minute % 30 == 0 else th["g10"] if minute % 10 == 0 else th["g5"]
            cv.polyline([X(minute), X(minute)], [m.PY0, m.PY1], shade, 1, z=1)
            if minute % 30 == 0:
                for yy in (64, 996):
                    put(X(minute), yy, m.hhmm(minute), "time", "sans", 15, th["ink"], ha="center")
        for s in scene["stations"]:
            cv.polyline([m.PX0, m.PX1], [Y(s["km"]), Y(s["km"])], th["row"], 1, z=1)
            put(m.PX0 - 12, Y(s["km"]), s["name"], "station", "sans-bold" if s["major"] else "sans", 15, th["ink"],
                ha="right")

        for kind in DRAW_ORDER:
            for tr in scene["trains"]:
                if tr["kind"] != kind:
                    continue
                pts = [p for p in m.path(tr, km)]
                xs, ys = [], []
                for i, (t, k) in enumerate(pts):
                    if t <= t1:
                        xs.append(X(t))
                        ys.append(Y(k))
                    else:                                   # still running when the chart ends: cut at the edge
                        ta, ka = pts[i - 1]
                        xs.append(X(t1))
                        ys.append(Y(ka + (k - ka) * (t1 - ta) / (t - ta)))
                        break
                cv.polyline(xs, ys, th[kind], LINE_PX, z=3 + DRAW_ORDER.index(kind))
                top = tr["dir"] == 1
                edge = m.PY0 if top else m.PY1
                sign = -1 if top else 1
                x0, tier = X(m.span(tr)[0]), tiers[tr["num"]]
                y = edge + sign * (18 + 19 * tier)
                if tier:
                    cv.polyline([x0, x0], [edge + sign * 3, y - sign * 9], th[kind], 1, z=2)
                put(x0, y, str(tr["num"]), "train", "sans-bold", FONT, th[kind], ha="center")

        put(24, 26, scene["title"], "chrome", "sans-bold", 17, th["ink"])
        lx = 900
        for kind in ("IC", "RE", "FR"):
            if any(t["kind"] == kind for t in scene["trains"]):
                cv.polyline([lx, lx + 34], [26, 26], th[kind], 3, z=3)
                put(lx + 42, 26, m.CLASS_NAME[kind], "chrome", "sans", 15, th["ink"])
                lx += 190

        cv.agg.draw()
        for lab in labels:
            lab["bbox"] = cv.bbox(lab.pop("_art"))
        png = cv.png()

    layout = {"width": W, "height": H, "labels": labels, "plot": [m.PX0, m.PY0, m.PX1, m.PY1], "t0": t0, "t1": t1,
              "rows": [round(Y(k), 2) for k in km], "colors": {k: rgb(th[k]) for k in ("IC", "RE", "FR")},
              "line_px": LINE_PX}
    return png, layout
