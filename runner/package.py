"""Secret scan + submission zip (rubric: runnable submission; the rules: keys stay out)."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from validate import hand_check as handcheck
from runner.config import Config
from runner.schema import load_tasks, task_fingerprint
from runner.output import UI

EXCLUDE_DIRS = {".git", "__pycache__", ".venv", "venv", "env", "node_modules", ".idea", ".vscode", "dist", ".pytest_cache"}
EXCLUDE_FILES = {".env", ".DS_Store"}
EXCLUDE_SUFFIX = {".pyc", ".zip"}
SECRET_PATTERNS = [
    ("OpenRouter key", re.compile(r"sk-or-[A-Za-z0-9_\-]{16,}")),
    ("Anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("generic key assignment", re.compile(r"""(?i)(api[_-]?key|secret|token)\s*[:=]\s*['"][A-Za-z0-9_\-]{24,}['"]""")),
]


def files_to_package(cfg: Config) -> list[Path]:
    out = []
    for p in sorted(cfg.root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(cfg.root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if p.name in EXCLUDE_FILES or p.suffix in EXCLUDE_SUFFIX:
            continue
        out.append(p)
    return out


def scan_for_secrets(cfg: Config, files: list[Path]) -> list[str]:
    hits = []
    for p in files:
        if p.stat().st_size > 5_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary file (images etc.)
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(f"{p.relative_to(cfg.root)}: looks like a {label}")
    return hits


def build_zip(cfg: Config, ui: UI, team: str, track: int, *, wrap_folder: bool = False,
              allow_unchecked: bool = False) -> Path | None:
    files = files_to_package(cfg)
    ui.info(f"{len(files)} file(s) would be packaged (.env, caches, .git, venvs and old zips are excluded)")

    hits = scan_for_secrets(cfg, files)
    if hits:
        for h in hits:
            ui.fail(h)
        ui.fail("SECRET(S) FOUND: remove them from the files above (use environment variables), then retry")
        return None
    ui.pass_("no API keys found in any packaged file")

    tasks, problems = load_tasks(cfg.tasks_file)
    if problems or not tasks:
        ui.fail("tasks.jsonl is missing, empty, or has problems; run the pipeline first")
        for p in problems:
            ui.detail(p)
        return None
    ledger = handcheck.load(cfg.tasks_dir)
    unchecked = [t.id for t in tasks
                 if handcheck.status(ledger, t.id, task_fingerprint(cfg.root, t))[0] != "ok"]
    if unchecked:
        msg = f"{len(unchecked)} task(s) not (currently) hand-checked: " + ", ".join(unchecked[:6])
        if allow_unchecked:
            ui.warn(msg + "  (continuing: --allow-unchecked)")
        else:
            ui.fail(msg + ". The rules require every task to be hand-checked. "
                          "Use `python -m validate.hand_check ID --by NAME`, or --allow-unchecked to override.")
            return None
    else:
        ui.pass_(f"all {len(tasks)} tasks are hand-checked")

    for f in ("responses.csv", "run_config.json"):
        p = cfg.results_dir / f
        has_rows = p.exists() and (f != "responses.csv" or len(p.read_text(encoding="utf-8").strip().splitlines()) > 1)
        note = "present" if has_rows else ("has a header but no responses" if p.exists() else "MISSING") + " (run the evaluation)"
        (ui.pass_ if has_rows else ui.warn)(f"results/{f} {note}")
    slides = cfg.path("slides_dir")
    if slides.is_dir() and any(slides.iterdir()):
        ui.pass_(f"{cfg.get('paths.slides_dir')}/ present (max five slides)")
    else:
        ui.warn(f"no slides found in {cfg.get('paths.slides_dir')}/ (the submission needs max five slides)")

    dist = cfg.path("dist_dir")
    dist.mkdir(parents=True, exist_ok=True)
    name = f"{team}_Track{track}"
    out = dist / f"{name}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            arc = p.relative_to(cfg.root)
            zf.write(p, (Path(name) / arc) if wrap_folder else arc)
    ui.pass_(f"wrote {out.relative_to(cfg.root)} ({out.stat().st_size / 1024:.0f} KB, "
             f"{'inside a folder named ' + name if wrap_folder else 'files at the zip root'})")
    ui.info("PROVISIONAL: confirm the zip's internal layout and the Track number with the organizers.")
    return out
