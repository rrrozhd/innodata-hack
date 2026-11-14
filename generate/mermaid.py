"""Turn Mermaid diagram source into a PNG by calling the Mermaid command-line tool (`mmdc`).

Mermaid is text, not a picture. The runner needs an image file, so something has to draw it. This helper calls
the official Mermaid CLI, which needs Node.js and a headless Chromium:

    npm install -g @mermaid-js/mermaid-cli

If `mmdc` is not on your PATH, set the MMDC environment variable to its full path. Calls the tool with the
documented flags (-i input, -o output, -t theme, -w width, -b background). NOTE: the plumbing is self-tested with a
stand-in `mmdc`; rendering with the real tool has to be tried on a machine that has it installed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def render_mermaid(source: str, out_path: Path, *, theme: str = "default", width: int = 1600,
                   background: str = "white", scale: int = 1, timeout: int = 90) -> None:
    """Render `source` (Mermaid text) to the image at `out_path` (use a .png path). Raises RuntimeError.

    `scale=1` keeps one image pixel per diagram pixel, so a 16px label stays 16px."""
    exe = shutil.which(os.environ.get("MMDC", "mmdc"))
    if not exe:
        raise RuntimeError("the Mermaid command-line tool 'mmdc' was not found. Install it with: "
                           "npm install -g @mermaid-js/mermaid-cli  (needs Node.js), or set the MMDC "
                           "environment variable to its full path")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "diagram.mmd"
        src.write_text(source, encoding="utf-8")
        cmd = [exe, "-i", str(src), "-o", str(out_path), "-t", theme, "-w", str(width), "-b", background, "-s", str(scale)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"mmdc took longer than {timeout}s (is Chromium able to start?)") from exc
    if proc.returncode != 0 or not out_path.exists():
        detail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise RuntimeError(f"mmdc failed (exit {proc.returncode}): {detail}")


def png_size(path: Path) -> tuple[int, int]:
    """(width, height) in pixels of a PNG file, read from its header. Raises ValueError if it is not a PNG."""
    head = Path(path).read_bytes()[:24]
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise ValueError(f"{path} is not a PNG file")
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
