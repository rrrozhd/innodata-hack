"""Render checks: the image exists and is sane, re-rendering is stable, and (via the generator's
verify_render hook) the picture really shows the data -- step 4 of the pipeline."""
from __future__ import annotations

import hashlib
import struct
import tempfile
from pathlib import Path

from generate.base import CheckResult
from validate.context import CheckContext


def image_info(path: Path) -> tuple[str, int | None, int | None]:
    """Return (format, width, height). Format is 'png', 'jpeg', or 'unknown'."""
    head = path.read_bytes()[:32]
    if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
        w, h = struct.unpack(">II", head[16:24])
        return "png", w, h
    if head.startswith(b"\xff\xd8"):
        return "jpeg", None, None
    return "unknown", None, None


def run(ctx: CheckContext) -> list[tuple[str, CheckResult]]:
    cfg, t = ctx.cfg, ctx.task
    img = ctx.image_abs
    if not img.exists():
        return [("image_exists", CheckResult.fail(f"image not found: {t.image_path}", where=t.image_path))]
    size = img.stat().st_size
    min_bytes = cfg.get("validation.min_image_bytes", 500)
    out: list[tuple[str, CheckResult]] = []
    if size < min_bytes:
        out.append(("image_valid", CheckResult.fail(
            f"image is only {size} bytes (looks blank or truncated)",
            expected=f">= {min_bytes} bytes", actual=f"{size} bytes", where=t.image_path)))
        return out
    fmt, w, h = image_info(img)
    ext = img.suffix.lower().lstrip(".")
    if ext in ("png", "jpg", "jpeg") and fmt == "unknown":
        out.append(("image_valid", CheckResult.fail(
            f"file ends in .{ext} but is not a real PNG/JPEG", where=t.image_path)))
        return out
    dims = f", {w}x{h}px" if w else ""
    out.append(("image_valid", CheckResult.ok(f"image exists and looks valid ({fmt}{dims}, {size} bytes)")))

    ctx.load_data()
    if ctx.generator is None or ctx.data_error:
        out.append(("render_reproducible", CheckResult.skip("needs the generator and a loadable data file")))
        out.append(("render_matches_data", CheckResult.skip("needs the generator and a loadable data file")))
        return out

    # Re-render to a temp file; identical bytes means the render is stable.
    try:
        with tempfile.TemporaryDirectory() as tmp:
            again = Path(tmp) / f"again.{ctx.generator.image_ext}"
            ctx.generator.render(ctx.data, again)
            same = hashlib.sha256(again.read_bytes()).hexdigest() == hashlib.sha256(img.read_bytes()).hexdigest()
        out.append(("render_reproducible", CheckResult.ok("re-rendering gives a byte-identical image") if same
                    else CheckResult.warn("re-rendering gives a slightly different file (metadata/fonts?). "
                                          "Fine if the picture is the same, but stable output is safer.")))
    except Exception as exc:  # noqa: BLE001
        out.append(("render_reproducible", CheckResult.fail(
            f"render() raised {type(exc).__name__}: {exc}", where="generator.render()")))

    try:
        out.append(("render_matches_data", ctx.generator.verify_render(ctx.data, img)))
    except Exception as exc:  # noqa: BLE001
        out.append(("render_matches_data", CheckResult.fail(
            f"verify_render() raised {type(exc).__name__}: {exc}", where="generator.verify_render()")))
    return out


def check_render(task, image_path) -> bool:
    """True if the image at image_path is valid, reproducible, and matches the task's data."""
    from validate.context import legacy_context
    ctx = legacy_context(task, image_path)
    return all(res.status != "fail" for _, res in run(ctx))
