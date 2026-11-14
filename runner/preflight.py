"""Stage 0: is this machine / repo ready to run? (rubric: runnable submission)

Key values are NEVER printed: we only report whether each environment variable is set.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

from runner.config import Config
from runner.context import RunContext, StageResult
from runner.output import UI
from generate.registry import Registry


def load_dotenv(root: Path) -> list[str]:
    """Optional convenience: read KEY=VALUE lines from .env into os.environ (never overrides
    variables that are already set). .env is git-ignored and excluded from the submission zip."""
    p = root / ".env"
    loaded = []
    if not p.exists():
        return loaded
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in ("'", '"'):
            end = value.find(value[0], 1)
            value = value[1:end] if end > 0 else value[1:]
        else:
            value = value.split(" #", 1)[0].strip()  # an inline comment ends an unquoted value
        key = key.strip()
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def _gitignored(root: Path, name: str) -> bool:
    gi = root / ".gitignore"
    if not gi.exists():
        return False
    return any(line.strip().rstrip("/") in (name, f"/{name}", f"{name}*") for line in gi.read_text("utf-8").splitlines())


def run_checks(cfg: Config, ui: UI, registry: Registry, *, live: bool, relaxed: bool = False) -> None:
    py = sys.version_info
    if py >= (3, 12):
        ui.pass_(f"Python {py.major}.{py.minor}.{py.micro}")
    elif py >= (3, 10):
        ui.warn(f"Python {py.major}.{py.minor}.{py.micro}: the plan is 3.12; this should still work")
    else:
        ui.fail(f"Python {py.major}.{py.minor}: need 3.10 or newer (3.12 recommended)")

    src = cfg.source.name if cfg.source else "built-in defaults (results/run_config.json not found)"
    ui.info(f"config: {src}   repo root: {cfg.root}")
    for note in cfg.notes:
        ui.warn(note)

    for d in cfg.get("submission.required_dirs", []):
        (ui.pass_ if (cfg.root / d).is_dir() else ui.fail)(f"folder {d}/ {'present' if (cfg.root / d).is_dir() else 'MISSING'}")
    for f in cfg.get("submission.required_files", []):
        (ui.pass_ if (cfg.root / f).is_file() else ui.fail)(f"file {f} {'present' if (cfg.root / f).is_file() else 'MISSING'}")
    ui.info(f"results folder name: {cfg.get('paths.results_dir')}/   tasks folder name: {cfg.get('paths.tasks_dir')}/   "
            "(PROVISIONAL: confirm against the organizer guide / format checker)")

    # Models
    models = cfg.models()
    placeholders = [m.label for m in models if m.is_placeholder]
    if placeholders:
        msg = ("model id(s) not set for: " + ", ".join(placeholders)
               + ". Copy the confirmed OpenRouter slugs from the organizer guide into results/run_config.json -> models[].openrouter_slug")
        (ui.fail if live else ui.warn)(msg)
    else:
        ui.pass_("model ids set: " + ", ".join(f"{m.label}={m.id}" for m in models))
    if len(models) != 2:
        ui.warn(f"{len(models)} model(s) configured; the brief asks for exactly two (Claude Haiku 5.5 and GPT-6 Luna)")

    # Settings required by the brief
    e = cfg.data["eval"]
    if e.get("temperature") != 0:
        ui.warn(f"eval.temperature is {e.get('temperature')}; the brief requires 0")
    else:
        ui.pass_("temperature 0")
    if str(e.get("reasoning_effort")).lower() != "high":
        ui.warn(f"eval.reasoning_effort is {e.get('reasoning_effort')!r}; the brief requires high")
    else:
        ui.pass_("reasoning effort high")
    ui.info(f"max_tokens (fixed, adequate): {e.get('max_tokens')}  [PROVISIONAL: raise it if any reply is truncated]")

    # Keys (names only)
    key_env = e.get("api_key_env", "OPENROUTER_API_KEY")
    if os.environ.get(key_env):
        ui.pass_(f"environment variable {key_env} is set (value not shown)")
    else:
        (ui.fail if live else ui.warn)(
            f"environment variable {key_env} is NOT set. {'Evaluation needs it.' if live else 'Needed for live evaluation.'} "
            f"Mac/Linux: export {key_env}=...   Windows (PowerShell): $env:{key_env}=\"...\"")
    ui.info("ANTHROPIC_API_KEY is " + ("set" if os.environ.get("ANTHROPIC_API_KEY") else "not set")
            + " (not used by this shell; only needed if your generators call Claude)")
    if (cfg.root / ".env").exists():
        if _gitignored(cfg.root, ".env"):
            ui.pass_(".env exists and is git-ignored (also excluded from the zip)")
        else:
            ui.fail(".env exists but is NOT in .gitignore: add a line '.env' before committing")

    # Generators
    n = len(registry.generators)
    if n:
        ui.pass_(f"{n} generator(s) registered: " + ", ".join(registry.generators))
    elif not relaxed:
        ui.warn("no generators registered yet. Create one: python -m generate.new_generator my_chart_name")
    for name in registry.disabled:
        ui.info(f"generator '{name}' is disabled (enabled = False)")
    for problem in registry.problems:
        ui.fail(f"generator problem: {problem}")

    # Optional libraries (information only)
    libs = []
    for mod in ("matplotlib", "numpy", "pandas", "PIL"):
        try:
            importlib.import_module(mod)
            libs.append(f"{mod}: yes")
        except Exception:
            libs.append(f"{mod}: no")
    ui.info("optional libraries for generators -> " + ", ".join(libs) + "  (the shell itself needs none)")
    ui.info(f"color output: {'on' if ui.color else 'off'}  (--no-color or NO_COLOR turns it off)")

    # Git state (information only)
    try:
        out = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=cfg.root,
                             capture_output=True, text=True, timeout=5)
        ui.info("git repository: " + ("yes" if out.returncode == 0 else "no (run `git init` when you are ready)"))
    except Exception:
        ui.info("git: not available on this machine")


# ---- stage entry point + small helpers (names kept from the original stub) ------------------------
NAME = "Preflight"
REQUIRED_PATHS = [Path("tasks/tasks.jsonl"), Path("results/run_config.json")]
REQUIRED_ENV_VARS = ["OPENROUTER_API_KEY"]


def check_python_version() -> bool:
    """True if Python is 3.12 or newer (the plan; 3.10+ still works, with a warning)."""
    return sys.version_info >= (3, 12)


def check_paths(paths: list[Path], root: Path | None = None) -> list[Path]:
    """Return the required paths that are missing."""
    base = root or Path.cwd()
    return [p for p in paths if not (base / p).exists()]


def check_env_vars(names: list[str]) -> dict[str, bool]:
    """Whether each variable is set. Never returns or prints values."""
    return {name: bool(os.environ.get(name)) for name in names}


def run(ctx: RunContext) -> StageResult:
    """Run all preflight checks and print one line per check."""
    selected = getattr(ctx.args, "stages_selected", [])
    live = 4 in selected and not ctx.dry_run and ctx.client is None
    run_checks(ctx.cfg, ctx.out, ctx.registry, live=live, relaxed=getattr(ctx.args, "relaxed", False))
    for rel in check_paths(REQUIRED_PATHS, ctx.cfg.root):
        ctx.out.fail(f"required file missing: {rel}")
    ctx.preflight_ok = ctx.out.current.counts["fail"] == 0
    return ctx.stage_result(NAME)
