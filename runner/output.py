"""Terminal output: ANSI colors, text markers, banners, summaries, and a plain-text log copy.

Color is used only when stdout is a terminal, NO_COLOR is unset, and --no-color was not passed.
Every status also carries a text marker, so the output still reads without color.
"""

import os
import re
import sys
from pathlib import Path
from typing import TextIO

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
RULE_WIDTH = 72


def color_enabled(no_color_flag: bool, stream: TextIO = sys.stdout) -> bool:
    """Return True if ANSI colors should be written to stream."""
    if no_color_flag or os.environ.get("NO_COLOR"):
        return False
    return stream.isatty()


class Output:
    """Writes colored lines to the terminal and an uncolored copy to a log file."""

    def __init__(
        self,
        color: bool,
        log_path: Path | None = None,
        stream: TextIO = sys.stdout,
    ) -> None:
        self.color = color
        self.stream = stream
        self._log: TextIO | None = None
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = log_path.open("w", encoding="utf-8")

    def paint(self, text: str, code: str) -> str:
        """Wrap text in an ANSI code when color is on."""
        return f"{code}{text}{RESET}" if self.color else text

    def line(self, text: str = "") -> None:
        """Write one line to the terminal and the log."""
        self.stream.write(text + "\n")
        self.stream.flush()
        if self._log is not None:
            self._log.write(ANSI_RE.sub("", text) + "\n")

    def pass_line(self, message: str) -> None:
        self.line(f"{self.paint('[PASS]', GREEN)} {message}")

    def fail_line(self, message: str) -> None:
        self.line(f"{self.paint('[FAIL]', RED)} {message}")

    def warn_line(self, message: str) -> None:
        self.line(f"{self.paint('[WARN]', YELLOW)} {message}")

    def info_line(self, message: str) -> None:
        self.line(f"{self.paint('[INFO]', CYAN)} {message}")

    def detail(self, message: str) -> None:
        """Secondary text, indented under the line above."""
        self.line(self.paint(f"       {message}", DIM))

    def banner(self, number: int, name: str) -> None:
        rule = "=" * RULE_WIDTH
        self.line()
        self.line(self.paint(rule, CYAN))
        self.line(self.paint(f"STAGE {number}: {name}", CYAN + BOLD))
        self.line(self.paint(rule, CYAN))

    def summary(self, name: str, passed: int, failed: int, warned: int) -> None:
        """One-line stage summary; the marker reflects the worst outcome."""
        text = f"{name} summary: {passed} passed, {failed} failed, {warned} warnings"
        if failed:
            self.fail_line(text)
        elif warned:
            self.warn_line(text)
        else:
            self.pass_line(text)

    def failure(
        self,
        task_id: str,
        check: str,
        expected: str,
        actual: str,
        where: str,
    ) -> None:
        """Report one failed check with enough detail to find and fix it."""
        self.fail_line(f"task={task_id} check={check}")
        self.detail(f"expected: {expected}")
        self.detail(f"actual:   {actual}")
        self.detail(f"look at:  {where}")

    def progress(
        self,
        task_id: str,
        model: str,
        elapsed_s: float,
        tokens_in: int,
        tokens_out: int,
        running_cost_usd: float,
    ) -> None:
        """One progress line for a model call, with the running cost tally."""
        self.info_line(
            f"task={task_id} model={model} elapsed={elapsed_s:.1f}s "
            f"tokens_in={tokens_in} tokens_out={tokens_out} "
            f"running_cost=${running_cost_usd:.4f}"
        )

    def close(self) -> None:
        if self._log is not None:
            self._log.close()
            self._log = None
