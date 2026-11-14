"""Deterministic matplotlib canvas addressed in image pixels (origin top-left, y down).

Nothing here reads global matplotlib state: fonts are loaded by file path, the
figure is built without pyplot, and drawing happens inside a frozen rc context.
"""
from __future__ import annotations

import hashlib
import io
from contextlib import contextmanager
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import numpy as np  # noqa: E402
from matplotlib import rc_context  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Polygon, Rectangle  # noqa: E402
from PIL import Image  # noqa: E402

DPI = 100
CAP_HEIGHT = 0.729  # DejaVu Sans capital height as a fraction of the em size

_FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
_FONT_FILES = {
    "sans": "DejaVuSans.ttf",
    "sans-bold": "DejaVuSans-Bold.ttf",
    "sans-oblique": "DejaVuSans-Oblique.ttf",
    "mono": "DejaVuSansMono.ttf",
    "mono-bold": "DejaVuSansMono-Bold.ttf",
}
_FROZEN_RC = {
    "text.antialiased": True,
    "text.hinting": "force_autohint",
    "text.hinting_factor": 8,
    "text.usetex": False,
    "path.snap": True,
    "path.simplify": False,
    "agg.path.chunksize": 0,
    "lines.scale_dashes": True,
    "axes.unicode_minus": False,
}


@contextmanager
def frozen():
    with rc_context(_FROZEN_RC):
        yield


def rgb(hex_color: str) -> list:
    h = hex_color.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def font(kind: str, size_px: float) -> FontProperties:
    return FontProperties(fname=str(_FONT_DIR / _FONT_FILES[kind]), size=size_px * 72.0 / DPI)


class Canvas:
    """A width x height pixel canvas. Shapes are drawn without antialiasing so
    their pixels have exact colours; text is antialiased."""

    def __init__(self, width: int, height: int, background: str):
        self.width, self.height = int(width), int(height)
        self.fig = Figure(figsize=(self.width / DPI, self.height / DPI), dpi=DPI, facecolor=background)
        self.agg = FigureCanvasAgg(self.fig)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, self.width)
        self.ax.set_ylim(self.height, 0)
        self.ax.axis("off")

    def rect(self, x0, y0, x1, y1, color, z=1):
        self.ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor=color, edgecolor="none",
                                    linewidth=0, antialiased=False, zorder=z))

    def polygon(self, points, color, z=1, antialiased=False):
        self.ax.add_patch(Polygon(points, closed=True, facecolor=color, edgecolor="none", linewidth=0,
                                  antialiased=antialiased, zorder=z))

    def polyline(self, xs, ys, color, width_px, z=3, dashes=None):
        line = Line2D(xs, ys, color=color, linewidth=width_px * 72.0 / DPI, antialiased=False,
                      solid_capstyle="butt", solid_joinstyle="miter", dash_capstyle="butt", zorder=z)
        if dashes:  # given in pixels; matplotlib scales the pattern by the line width
            line.set_dashes([d / width_px for d in dashes])
        self.ax.add_line(line)
        return line

    def curve(self, xs, ys, color, width_px, z=3):
        """A smooth antialiased line, for curved wires and connector lines."""
        line = Line2D(xs, ys, color=color, linewidth=width_px * 72.0 / DPI, antialiased=True,
                      solid_capstyle="butt", solid_joinstyle="miter", zorder=z)
        self.ax.add_line(line)
        return line

    def outline(self, x0, y0, x1, y1, face, edge, width_px, z=2, rounding=0):
        """A filled box with a border."""
        if rounding:
            from matplotlib.patches import FancyBboxPatch
            patch = FancyBboxPatch((x0 + rounding / 4, y0 + rounding / 4), x1 - x0 - rounding / 2,
                                   y1 - y0 - rounding / 2, boxstyle=f"round,pad={rounding / 4},rounding_size={rounding}",
                                   facecolor=face, edgecolor=edge, linewidth=width_px * 72.0 / DPI, zorder=z)
        else:
            patch = Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor=face, edgecolor=edge,
                              linewidth=width_px * 72.0 / DPI, zorder=z)
        self.ax.add_patch(patch)

    def text(self, x, y, s, kind="sans", size_px=14, color="#000000", ha="left", va="center", z=6):
        return self.ax.text(x, y, s, fontproperties=font(kind, size_px), color=color, ha=ha, va=va, zorder=z)

    def bbox(self, artist) -> list:
        """Bounding box of a drawn text artist as [x0, y0, x1, y1] in image pixels."""
        ext = artist.get_window_extent(self.agg.get_renderer())
        return [round(float(ext.x0), 2), round(self.height - float(ext.y1), 2),
                round(float(ext.x1), 2), round(self.height - float(ext.y0), 2)]

    def png(self) -> bytes:
        buf = io.BytesIO()
        self.fig.savefig(buf, format="png", dpi=DPI, facecolor=self.fig.get_facecolor(),
                         metadata={"Software": None})
        return buf.getvalue()


def load_rgb(png_bytes: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGB"))


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pixel_sha256(png_bytes: bytes) -> str:
    """Hash of the decoded pixels, so two PNG files that differ only in
    compression still compare equal."""
    arr = load_rgb(png_bytes)
    h = hashlib.sha256()
    h.update(f"{arr.shape[0]}x{arr.shape[1]}".encode())
    h.update(arr.tobytes())
    return h.hexdigest()
