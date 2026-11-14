"""Stage 4: send every valid task to every model, save raw replies, and track spend.

Rules baked in (from the brief): both models run on every valid task; temperature 0; high
reasoning; a fixed token limit; raw responses + config saved; API errors and token cutoffs are
NOT accepted, so they are flagged as invalid and re-run on the next evaluate (valid replies are
cached, so re-running costs nothing for them).
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import mimetypes
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from runner.config import Config, ModelSpec, deep_merge
from runner.schema import Task, file_sha256
from runner.output import UI


@dataclass
class EvalRecord:
    task_id: str
    model_label: str
    model_id: str
    status: str
    text: str = ""
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost_usd: float | None = None
    latency_s: float | None = None
    attempts: int = 0
    raw_path: str = ""
    timestamp: str = ""
    error: str = ""
    cached: bool = False


def prompt_text(cfg: Config, task: Task) -> str:
    return f"{task.question}\n\n{cfg.get('eval.answer_instruction')}"


def image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def build_messages(cfg: Config, task: Task) -> list[dict]:
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt_text(cfg, task)},
            {"type": "image_url", "image_url": {"url": image_data_url(cfg.root / task.image_path)}},
        ],
    }]


def request_key(cfg: Config, task: Task, model: ModelSpec) -> str:
    """Cache key: changes whenever the model, settings, question or image change."""
    h = hashlib.sha256()
    h.update(model.id.encode())
    h.update(json.dumps(model.extra_body, sort_keys=True).encode())
    h.update(cfg.eval_settings_fingerprint().encode())
    h.update(prompt_text(cfg, task).encode())
    h.update(file_sha256(cfg.root / task.image_path).encode())
    return h.hexdigest()[:16]


def raw_file(cfg: Config, model: ModelSpec, task_id: str) -> Path:
    return cfg.results_dir / "raw" / model.slug / f"{task_id}.json"


def _record_from_raw(cfg: Config, model: ModelSpec, task_id: str, blob: dict, cached: bool) -> EvalRecord:
    usage = blob.get("usage", {})
    return EvalRecord(
        task_id=task_id, model_label=model.label, model_id=blob.get("model_id", model.id),
        status=blob.get("status", "api_error"), text=blob.get("text", ""),
        finish_reason=blob.get("finish_reason"), prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"), reasoning_tokens=usage.get("reasoning_tokens"),
        cost_usd=blob.get("cost_usd"), latency_s=blob.get("latency_s"), attempts=blob.get("attempts", 0),
        raw_path=str(raw_file(cfg, model, task_id).relative_to(cfg.root)),
        timestamp=blob.get("timestamp", ""), error=blob.get("error", ""), cached=cached,
    )


def load_records(cfg: Config, tasks: list[Task], models: list[ModelSpec]) -> list[EvalRecord]:
    """Read previously saved raw replies (no API calls). Missing ones become status 'not_run'."""
    records = []
    for model in models:
        for task in tasks:
            p = raw_file(cfg, model, task.id)
            if p.exists():
                try:
                    blob = json.loads(p.read_text("utf-8"))
                    if blob.get("request_key") != request_key(cfg, task, model):
                        # saved for a different image/question/answer/settings: never score it against this task
                        records.append(EvalRecord(task.id, model.label, model.id, "not_run"))
                        continue
                    records.append(_record_from_raw(cfg, model, task.id, blob, True))
                    continue
                except json.JSONDecodeError:
                    pass
            records.append(EvalRecord(task.id, model.label, model.id, "not_run"))
    return records


def _fix_hint(status: str) -> str:
    return {
        "truncated": "raise max_tokens in results/run_config.json, then run stage 4 again (it re-runs only invalid replies)",
        "empty": "run evaluate again; if it keeps happening check the model slug / extra_body in results/run_config.json",
        "filtered": "inspect results/raw/<model>/<task>.json; the provider stopped the reply",
        "api_error": "check your key, budget and model id; then run evaluate again (valid replies are cached)",
    }.get(status, "run evaluate again")


def run_evaluation(cfg: Config, ui: UI, tasks: list[Task], client, models: list[ModelSpec], *,
                   force: bool = False, dry_run: bool = False) -> tuple[list[EvalRecord], dict]:
    settings = {
        "temperature": cfg.get("eval.temperature"),
        "reasoning_effort": cfg.get("eval.reasoning_effort"),
        "max_tokens": cfg.get("eval.max_tokens"),
    }
    budget = cfg.get("eval.budget_usd")
    ui.kv("models", ", ".join(f"{m.label} ({m.id})" for m in models))
    ui.kv("settings", f"temperature={settings['temperature']}  reasoning={settings['reasoning_effort']}  "
                      f"max_tokens={settings['max_tokens']}")
    ui.kv("tasks", f"{len(tasks)} valid task(s) x {len(models)} model(s) = {len(tasks) * len(models)} call(s)")
    ui.kv("budget", f"stop at ${budget:.2f} spent this run" if budget else "no per-run budget set (eval.budget_usd)")
    ui.blank()

    stats = {"spent": 0.0, "unknown_cost_calls": 0, "calls": 0, "cached": 0, "stopped_for_budget": False}
    records: list[EvalRecord] = []
    stop = False

    for model in models:
        if stop:
            break
        ui.heading(f"model: {model.label}")
        for task in tasks:
            rawp = raw_file(cfg, model, task.id)
            key = request_key(cfg, task, model)
            if not force and rawp.exists():
                try:
                    blob = json.loads(rawp.read_text("utf-8"))
                except json.JSONDecodeError:
                    blob = {}
                if blob.get("status") == "ok" and blob.get("request_key") == key:
                    records.append(_record_from_raw(cfg, model, task.id, blob, True))
                    stats["cached"] += 1
                    ui.skip(f"{model.label:<18} {task.id:<22} cached valid reply (no call made)")
                    continue
            if dry_run:
                size_kb = (cfg.root / task.image_path).stat().st_size / 1024
                ui.info(f"{model.label:<18} {task.id:<22} WOULD CALL (image {size_kb:.0f} KB, "
                        f"prompt {len(prompt_text(cfg, task))} chars)")
                records.append(EvalRecord(task.id, model.label, model.id, "not_run"))
                continue
            if budget and stats["spent"] >= budget:
                ui.fail(f"budget of ${budget:.2f} reached; stopping. Raise eval.budget_usd to continue.")
                stats["stopped_for_budget"] = True
                stop = True
                break

            messages = build_messages(cfg, task)
            comp = client.complete(model, messages, settings,
                                   meta={"task_id": task.id, "expected": task.answer})
            stats["calls"] += 1
            if comp.cost_usd is None:
                comp.cost_usd = _cost_from_pricing(cfg, model, comp)
            if comp.cost_usd is not None:
                stats["spent"] += float(comp.cost_usd)
            else:
                stats["unknown_cost_calls"] += 1
            ts = dt.datetime.now().isoformat(timespec="seconds")
            blob = {
                "task_id": task.id, "model_label": model.label, "model_id": model.id,
                "request_key": key, "status": comp.status, "finish_reason": comp.finish_reason,
                "text": comp.text, "error": comp.error, "cost_usd": comp.cost_usd,
                "latency_s": comp.latency_s, "attempts": comp.attempts, "timestamp": ts,
                "usage": {"prompt_tokens": comp.prompt_tokens, "completion_tokens": comp.completion_tokens,
                          "reasoning_tokens": comp.reasoning_tokens},
                "request": {**settings, "prompt_text": prompt_text(cfg, task), "image": task.image_path},
            }
            rawp.parent.mkdir(parents=True, exist_ok=True)
            rawp.write_text(json.dumps(blob, indent=2, ensure_ascii=False), encoding="utf-8")
            rec = _record_from_raw(cfg, model, task.id, blob, False)
            records.append(rec)

            cost_txt = f"${comp.cost_usd:.4f}" if comp.cost_usd is not None else "$?"
            tokens = f"in={comp.prompt_tokens} out={comp.completion_tokens} reasoning={comp.reasoning_tokens}"
            tally = f"run total ${stats['spent']:.4f}" + (
                f" (+{stats['unknown_cost_calls']} call(s) with unknown cost)" if stats["unknown_cost_calls"] else "")
            line = (f"{model.label:<18} {task.id:<22} {comp.status:<9} {tokens}  "
                    f"{(comp.latency_s or 0):.1f}s  {cost_txt}  [{tally}]")
            if comp.status == "ok":
                ui.pass_(line)
            else:
                ui.fail(line)
                ui.detail(f"{comp.error or comp.status}")
                ui.detail(f"fix: {_fix_hint(comp.status)}")

    if not dry_run and stats["calls"]:
        write_run_config(cfg, models, settings, tasks, stats)
        ui.info(f"made {stats['calls']} call(s), reused {stats['cached']} cached; spent ${stats['spent']:.4f}"
                + (f" (cost unknown for {stats['unknown_cost_calls']} call(s))" if stats["unknown_cost_calls"] else ""))
        ui.info("wrote results/run_config.json and raw replies under results/raw/")
    elif not dry_run:
        ui.info(f"no new calls needed ({stats['cached']} cached valid reply/replies reused)")
    return records, stats


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _cost_from_pricing(cfg: Config, model: ModelSpec, comp) -> float | None:
    """Fallback when OpenRouter reports no cost: tokens x the hand-copied prices in run_config.json."""
    price = (cfg.get("pricing", {}) or {}).get(model.label) or {}
    pin, pout = _num(price.get("usd_per_million_input")), _num(price.get("usd_per_million_output"))
    if pin is None or pout is None or comp.prompt_tokens is None or comp.completion_tokens is None:
        return None
    return (comp.prompt_tokens * pin + comp.completion_tokens * pout) / 1_000_000


def _git_commit(root: Path) -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def write_run_config(cfg: Config, models: list[ModelSpec], settings: dict, tasks: list[Task], stats: dict) -> None:
    """Record this run in results/run_config.json WITHOUT disturbing the file's own keys.

    Keys the team maintains (models, pricing, format_checker, ...) are left alone. The record keys
    run_date, git_commit, prompt_version, settings, last_run and environment are added or updated.
    reasoning_effort / max_tokens are filled in only when they are still placeholders (the run used the
    documented defaults, so the file now says what was actually used).
    """
    p = cfg.source or (cfg.results_dir / "run_config.json")  # the file the config was read from
    try:
        blob = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        if not isinstance(blob, dict):
            blob = {}
    except json.JSONDecodeError:
        blob = {}
    for key in ("reasoning_effort", "max_tokens"):
        if isinstance(blob.get(key), str) and blob[key].strip().upper().startswith("PLACEHOLDER"):
            blob[key] = settings[key]
    blob.update({
        "run_date": dt.datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git_commit(cfg.root) or blob.get("git_commit", "unknown"),
        "prompt_version": (blob["prompt_version"] if isinstance(blob.get("prompt_version"), str)
                           and not blob["prompt_version"].strip().upper().startswith("PLACEHOLDER")
                           else cfg.eval_settings_fingerprint()),  # a hand-set version label is kept
        "settings": {**settings, "request_timeout_s": cfg.get("eval.request_timeout_s"),
                     "max_api_retries": cfg.get("eval.max_api_retries"), "base_url": cfg.get("eval.base_url")},
        "answer_instruction": cfg.get("eval.answer_instruction"),
        "tasks_evaluated": [t.id for t in tasks],
        "last_run": {"calls_made": stats["calls"], "cached_reused": stats["cached"],
                     "spent_usd": round(stats["spent"], 6),
                     "calls_with_unknown_cost": stats["unknown_cost_calls"]},
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
        "policy": "API errors and token cutoffs are invalid and re-run; they are never scored.",
    })
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(blob, indent=2), encoding="utf-8")


# ---- original stub API (kept so `python -m eval.call_models` and old imports still work) ----------
_ROOT = Path(__file__).resolve().parent.parent
RUN_CONFIG_PATH = _ROOT / "results" / "run_config.json"
RAW_DIR = Path("results/raw")


def load_run_config(path: Path = RUN_CONFIG_PATH) -> dict:
    """Load results/run_config.json as a plain dict."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def call_model(model_slug: str, prompt: str, image_path: Path, config: dict) -> dict:
    """One-off live call (not cached): returns the reply as a dict. Needs OPENROUTER_API_KEY."""
    import os
    from eval.client import OpenRouterClient
    from runner.config import Config, from_run_config, is_placeholder
    if is_placeholder(model_slug) or not model_slug:
        raise ValueError(f"model slug {model_slug!r} is a placeholder; set the real OpenRouter slug first")
    cfg = Config(Path.cwd(), deep_merge(DEFAULTS_FOR_CALL(), from_run_config(config)[0]))
    key = os.environ.get(cfg.get("eval.api_key_env", "OPENROUTER_API_KEY"))
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    client = OpenRouterClient(cfg.get("eval.base_url"), key, timeout=cfg.get("eval.request_timeout_s", 300),
                              max_retries=cfg.get("eval.max_api_retries", 3), backoff=cfg.get("eval.retry_backoff_s", 5))
    messages = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": image_data_url(Path(image_path))}}]}]
    settings = {k: cfg.get(f"eval.{k}") for k in ("temperature", "reasoning_effort", "max_tokens")}
    comp = client.complete(ModelSpec(model_slug, model_slug), messages, settings, meta={})
    return {"status": comp.status, "text": comp.text, "finish_reason": comp.finish_reason,
            "error": comp.error, "cost_usd": comp.cost_usd}


def DEFAULTS_FOR_CALL() -> dict:
    from runner.config import DEFAULTS
    return DEFAULTS


def save_raw_response(task_id: str, model_label: str, reply: dict, root: Path | None = None) -> Path:
    """Write one raw reply to results/raw/<model>/<task_id>.json and return its path."""
    slug = ModelSpec(model_label, model_label).slug
    path = (root or _ROOT) / RAW_DIR / slug / f"{task_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reply, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> None:
    """Run every task against every configured model (same as: python run_tests.py --stage 1-4)."""
    import sys as _sys
    from pathlib import Path as _P
    root = _P(__file__).resolve().parent.parent
    _sys.path.insert(0, str(root))
    import run_tests
    raise SystemExit(run_tests.main(["--stage", "0-4"] + _sys.argv[1:]))


if __name__ == "__main__":
    main()
