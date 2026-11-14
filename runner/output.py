"""Terminal output: colors, [PASS]/[FAIL] markers, stage banners, and a plain-text log copy.

`UI` is the full implementation. `Output` (bottom of the file) keeps the method names the original
runner stubs used (pass_line, fail_line, ...), so either style works.

Colors:  green = pass, red = fail, yellow = warn, cyan = stage/info, gray = detail.
Color is never the only signal: every status line also carries a text marker.
Color is disabled by --no-color, by the NO_COLOR environment variable, or when output is
not a terminal (set FORCE_COLOR=1 to force it on).
Everything printed is also written, without color codes, to the log file (if one is set).
"""
from __future__ import annotations

import contextlib
import os
import re
import sys
import time
from pathlib import Path

_CODES = {
    "reset": "\x1b[0m", "bold": "\x1b[1m", "dim": "\x1b[2m",
    "red": "\x1b[31m", "green": "\x1b[32m", "yellow": "\x1b[33m",
    "blue": "\x1b[34m", "magenta": "\x1b[35m", "cyan": "\x1b[36m", "gray": "\x1b[90m",
}
_STRIP = re.compile(r"\x1b\[[0-9;]*m")
WIDTH = 78


def strip_ansi(text: str) -> str:
    return _STRIP.sub("", text)


class StageTally:
    def __init__(self, number: int, name: str):
        self.number = number
        self.name = name
        self.counts = {"pass": 0, "fail": 0, "warn": 0, "skip": 0}
        self.started = time.time()


class UI:
    MARKERS = {
        "pass": ("[PASS]", "green"),
        "fail": ("[FAIL]", "red"),
        "warn": ("[WARN]", "yellow"),
        "skip": ("[SKIP]", "gray"),
        "info": ("[INFO]", "cyan"),
    }

    def __init__(self, color: bool | None = None, log_path: Path | None = None, verbose: bool = False):
        self.verbose = verbose
        self.color = self._decide_color(color)
        self._quiet = 0
        self._log = None
        self.current: StageTally | None = None
        self.history: list[StageTally] = []
        self.totals = {"pass": 0, "fail": 0, "warn": 0, "skip": 0}
        if self.color and os.name == "nt":
            os.system("")  # enables ANSI escape handling on Windows terminals
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = open(log_path, "a", encoding="utf-8")

    # ---- plumbing -------------------------------------------------------------------------
    @staticmethod
    def _decide_color(color: bool | None) -> bool:
        if color is False or os.environ.get("NO_COLOR"):
            return False
        if os.environ.get("FORCE_COLOR"):
            return True
        return sys.stdout.isatty()

    def c(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        return "".join(_CODES[s] for s in styles) + text + _CODES["reset"]

    def emit(self, line: str = "") -> None:
        if self._quiet:
            return
        print(line, flush=True)
        if self._log:
            self._log.write(strip_ansi(line) + "\n")
            self._log.flush()

    @contextlib.contextmanager
    def quiet(self):
        """Suppress output (and tallies) inside the block, e.g. for silent background validation."""
        self._quiet += 1
        try:
            yield
        finally:
            self._quiet -= 1

    def close(self) -> None:
        if self._log:
            self._log.close()
            self._log = None

    # ---- status lines ---------------------------------------------------------------------
    def _status(self, status: str, msg: str, indent: int = 0) -> None:
        marker, color = self.MARKERS[status]
        if not self._quiet and status in self.totals:
            self.totals[status] += 1
            if self.current:
                self.current.counts[status] += 1
        self.emit(" " * indent + self.c(marker, color, "bold") + " " + msg)

    def pass_(self, msg: str, indent: int = 0) -> None:
        self._status("pass", msg, indent)

    def fail(self, msg: str, indent: int = 0) -> None:
        self._status("fail", msg, indent)

    def warn(self, msg: str, indent: int = 0) -> None:
        self._status("warn", msg, indent)

    def skip(self, msg: str, indent: int = 0) -> None:
        self._status("skip", msg, indent)

    def info(self, msg: str, indent: int = 0) -> None:
        self._status("info", msg, indent)

    def status(self, status: str, msg: str, indent: int = 0) -> None:
        self._status(status, msg, indent)

    def detail(self, msg: str, indent: int = 8) -> None:
        """Secondary detail, printed in gray under the status line it explains."""
        for part in str(msg).splitlines() or [""]:
            self.emit(" " * indent + self.c(part, "gray"))

    def verbose_detail(self, msg: str, indent: int = 8) -> None:
        if self.verbose:
            self.detail(msg, indent)

    def expected_actual(self, expected, actual, where: str = "", indent: int = 8) -> None:
        pad = " " * indent
        self.emit(pad + self.c("expected: ", "bold") + self.c(str(expected), "green"))
        self.emit(pad + self.c("actual:   ", "bold") + self.c(str(actual), "red"))
        if where:
            self.emit(pad + self.c("look at:  ", "bold") + self.c(where, "gray"))

    def blank(self) -> None:
        self.emit()

    def heading(self, title: str) -> None:
        self.emit(self.c(f"-- {title} --", "bold"))

    def kv(self, key: str, value, indent: int = 2) -> None:
        self.emit(" " * indent + self.c(f"{key}: ", "bold") + str(value))

    def table(self, headers: list[str], rows: list[list[str]], indent: int = 2) -> None:
        cols = len(headers)
        widths = [len(strip_ansi(str(h))) for h in headers]
        for row in rows:
            for i in range(cols):
                widths[i] = max(widths[i], len(strip_ansi(str(row[i]))))

        def fmt(cells: list[str], bold: bool = False) -> str:
            out = []
            for i, cell in enumerate(cells):
                cell = str(cell)
                pad = " " * (widths[i] - len(strip_ansi(cell)))
                out.append((self.c(cell, "bold") if bold else cell) + pad)
            return " " * indent + "  ".join(out)

        self.emit(fmt(headers, bold=True))
        self.emit(" " * indent + self.c("  ".join("-" * w for w in widths), "gray"))
        for row in rows:
            self.emit(fmt(row))

    # ---- stages ---------------------------------------------------------------------------
    def banner(self, number: int, name: str, blurb: str = "", total: int | None = None) -> None:
        bar = "=" * WIDTH
        self.emit()
        self.emit(self.c(bar, "cyan"))
        label = f"{number}/{total}" if total else f"{number}"
        head = self.c(f" STAGE {label}  {name.upper()}", "cyan", "bold")
        if blurb:
            head += self.c(f"   {blurb}", "gray")
        self.emit(head)
        self.emit(self.c(bar, "cyan"))
        self.current = StageTally(number, name)

    def end_stage(self) -> StageTally | None:
        tally = self.current
        if tally is None:
            return None
        counts = tally.counts
        secs = time.time() - tally.started
        self.emit(self.c("-" * WIDTH, "gray"))
        self.emit(
            self.c(f" Stage {tally.number} ({tally.name}) summary: ", "bold")
            + self.c(f"{counts['pass']} pass", "green") + "  |  "
            + self.c(f"{counts['warn']} warn", "yellow") + "  |  "
            + self.c(f"{counts['fail']} fail", "red") + "  |  "
            + self.c(f"{counts['skip']} skip", "gray")
            + self.c(f"   ({secs:.1f}s)", "gray")
        )
        self.history.append(tally)
        self.current = None
        return tally


def color_enabled(no_color_flag: bool, stream=None) -> bool:
    """Return True if ANSI colors should be written (kept for the original runner API)."""
    if no_color_flag or os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return (stream or sys.stdout).isatty()


RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
RED, GREEN, YELLOW, CYAN = "\033[31m", "\033[32m", "\033[33m", "\033[36m"


class Output(UI):
    """UI plus the original runner's method names."""

    def paint(self, text: str, code: str) -> str:
        """Wrap text in an ANSI code when color is on."""
        return f"{code}{text}{RESET}" if self.color else text

    def __init__(self, color: bool | None = None, log_path: Path | None = None, verbose: bool = False):
        super().__init__(color=color, log_path=log_path, verbose=verbose)

    def line(self, text: str = "") -> None:
        self.emit(text)

    def pass_line(self, message: str) -> None:
        self.pass_(message)

    def fail_line(self, message: str) -> None:
        self.fail(message)

    def warn_line(self, message: str) -> None:
        self.warn(message)

    def info_line(self, message: str) -> None:
        self.info(message)

    def skip_line(self, message: str) -> None:
        self.skip(message)

    def summary(self, name: str, passed: int, failed: int, warned: int) -> None:
        text = f"{name} summary: {passed} passed, {failed} failed, {warned} warnings"
        (self.fail if failed else self.warn if warned else self.pass_)(text)

    def failure(self, task_id: str, check: str, expected: str, actual: str, where: str) -> None:
        self.fail(f"task={task_id} check={check}")
        self.expected_actual(expected, actual, where)

    def progress(self, task_id: str, model: str, elapsed_s: float, tokens_in: int, tokens_out: int,
                 running_cost_usd: float) -> None:
        self.info(f"task={task_id} model={model} elapsed={elapsed_s:.1f}s tokens_in={tokens_in} "
                  f"tokens_out={tokens_out} running_cost=${running_cost_usd:.4f}")
