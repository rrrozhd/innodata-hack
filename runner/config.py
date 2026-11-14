"""Configuration: built-in defaults plus results/run_config.json.

results/run_config.json is the repo's run configuration file. Its original keys are kept
(models[].name / openrouter_slug, temperature, reasoning_effort, max_tokens, pricing,
format_checker). Any value that still starts with "PLACEHOLDER" counts as "not set": models
with a placeholder slug cannot be called live, and a placeholder reasoning_effort / max_tokens falls
back to the documented default (reported as a warning by preflight). Extra optional keys
(seed, task_count_target, team_name, track_number, eval{...}, budget_usd ...) override DEFAULTS.
Paths are relative to the repo root.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

PLACEHOLDER_MODEL_ID = "PLACEHOLDER"
DEFAULT_CONFIG_RELPATH = "results/run_config.json"


def is_placeholder(value) -> bool:
    """True for a string that starts with PLACEHOLDER (any case): the value was never filled in."""
    return isinstance(value, str) and value.strip().upper().startswith("PLACEHOLDER")

DEFAULTS: dict = {
    "team_name": "TeamName",
    "track_number": 2,  # PROVISIONAL: confirm with organizers which track number is "visual"
    "seed": 1234,
    "task_count_target": 10,
    "paths": {
        # PROVISIONAL folder names: confirm against the organizer guide / format checker.
        "tasks_dir": "tasks",
        "results_dir": "results",
        "slides_dir": "slides",
        "dist_dir": "dist",
    },
    "models": [
        {"label": "Claude Haiku 5.5", "id": PLACEHOLDER_MODEL_ID, "extra_body": {}},
        {"label": "GPT-6 Luna", "id": PLACEHOLDER_MODEL_ID, "extra_body": {}},
    ],
    "eval": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "temperature": 0,
        "reasoning_effort": "high",
        "max_tokens": 16000,  # PROVISIONAL: "fixed, adequate". Raise it if you see truncations.
        "request_timeout_s": 300,
        "max_api_retries": 3,
        "retry_backoff_s": 5,
        "budget_usd": None,  # hard stop for one evaluation run, e.g. 40.0. null = no limit
        "answer_instruction": (
            "Think it through, then finish your reply with one final line in exactly this "
            "form:\nFINAL ANSWER: <your answer>"
        ),
    },
    "validation": {
        "strict": False,
        "min_image_bytes": 500,
        "leak_ratio_fail": 0.6,
        "leak_ratio_warn": 0.3,
        "leak_min_values": 3,
        "near_duplicate_ratio": 0.9,
        "text_only_baseline": False,
    },
    "variation": {"min_distinct_visual_types": 2, "max_share_single_generator": 0.7},
    "submission": {
        # Command to run the organizers' format checker. {root} is replaced by the repo root.
        # Normally set through run_config.json -> format_checker {"enabled": true, "command": "..."}.
        # Empty = skip (shown as a warning).
        "format_checker_command": "",
        "required_files": ["README.md"],
        "required_dirs": ["runner", "generate", "validate", "eval", "tasks", "results"],
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class ConfigError(Exception):
    pass


def from_run_config(raw: dict) -> tuple[dict, list[str]]:
    """Translate results/run_config.json into the internal config layout.

    Returns (config overrides, notes). Notes say which placeholder values fell back to defaults.
    """
    notes: list[str] = []
    skip = {"models", "temperature", "reasoning_effort", "max_tokens", "pricing", "format_checker"}
    out = {k: v for k, v in raw.items() if k not in skip and not k.startswith("_") and not is_placeholder(v)}
    models = []
    for m in raw.get("models") or []:
        if not isinstance(m, dict):
            notes.append(f"run_config.json models[] entry {m!r} is not an object; ignored")
            continue
        label = m.get("name") or m.get("label") or "model"
        models.append({"label": label, "id": m.get("openrouter_slug") or m.get("id") or "",
                       "extra_body": m.get("extra_body") or {}})
    if models:
        out["models"] = models
    ev = dict(out.get("eval") or {})
    budget = raw.get("budget_usd")
    if isinstance(budget, (int, float)) and not isinstance(budget, bool):
        ev["budget_usd"] = budget  # a top-level budget_usd is the hard stop for one evaluation run
    out.pop("budget_usd", None)
    temp = raw.get("temperature")
    if isinstance(temp, (int, float)) and not isinstance(temp, bool):
        ev["temperature"] = temp
    for key, default in (("reasoning_effort", DEFAULTS["eval"]["reasoning_effort"]),
                         ("max_tokens", DEFAULTS["eval"]["max_tokens"])):
        value = raw.get(key)
        if value is None:
            continue
        if is_placeholder(value):
            notes.append(f"run_config.json {key} is still a placeholder; using the default {default!r}")
        else:
            ev[key] = value
    out["eval"] = ev
    if isinstance(raw.get("pricing"), dict):
        out["pricing"] = raw["pricing"]
    fc = raw.get("format_checker") or {}
    if fc.get("enabled") and fc.get("command") and not is_placeholder(fc["command"]):
        out.setdefault("submission", {})["format_checker_command"] = fc["command"]
    return out, notes


class Config:
    def __init__(self, root: Path, data: dict, source: Path | None = None, notes: list[str] | None = None):
        self.root = Path(root).resolve()
        self.data = data
        self.source = source
        self.notes = notes or []

    @classmethod
    def load(cls, root: Path, path: Path | None = None, overrides: dict | None = None) -> "Config":
        path = Path(path) if path else (Path(root) / DEFAULT_CONFIG_RELPATH)
        data = copy.deepcopy(DEFAULTS)
        source = None
        notes: list[str] = []
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
            if not isinstance(raw, dict):
                raise ConfigError(f"{path} must contain a JSON object")
            translated, notes = from_run_config(raw)
            data = deep_merge(data, translated)
            source = path
        if overrides:
            data = deep_merge(data, overrides)
        return cls(root, data, source, notes)

    def get(self, dotted: str, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def path(self, key: str) -> Path:
        return self.root / self.data["paths"][key]

    @property
    def tasks_dir(self) -> Path:
        return self.path("tasks_dir")

    @property
    def results_dir(self) -> Path:
        return self.path("results_dir")

    @property
    def tasks_file(self) -> Path:
        return self.tasks_dir / "tasks.jsonl"

    def models(self) -> list["ModelSpec"]:
        out = []
        for entry in self.data["models"]:
            out.append(ModelSpec(entry["label"], entry["id"], entry.get("extra_body") or {}))
        return out

    def eval_settings_fingerprint(self) -> str:
        e = self.data["eval"]
        keys = ("temperature", "reasoning_effort", "max_tokens", "answer_instruction")
        blob = json.dumps({k: e.get(k) for k in keys}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]


class ModelSpec:
    def __init__(self, label: str, model_id: str, extra_body: dict | None = None):
        self.label = label
        self.id = model_id
        self.extra_body = extra_body or {}

    @property
    def is_placeholder(self) -> bool:
        return (not self.id) or is_placeholder(self.id)

    @property
    def slug(self) -> str:
        import re
        return re.sub(r"[^A-Za-z0-9._-]+", "_", self.label)
