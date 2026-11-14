"""Shared context handed to every check."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runner.config import Config
from runner.schema import Task
from generate.base import TaskGenerator


@dataclass
class CheckContext:
    cfg: Config
    task: Task
    generator: TaskGenerator | None
    ledger: dict
    fingerprint: str
    data: Any = None
    data_error: str = ""
    _loaded: bool = field(default=False, repr=False)

    @property
    def data_abs(self) -> Path:
        return self.cfg.root / self.task.data_path

    @property
    def image_abs(self) -> Path:
        return self.cfg.root / self.task.image_path

    def load_data(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            self.data = json.loads(self.data_abs.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.data_error = f"data file not found: {self.task.data_path}"
        except json.JSONDecodeError as exc:
            self.data_error = f"data file is not valid JSON: {exc}"


def legacy_context(task, image_path=None) -> "CheckContext":
    """Build a CheckContext from a task record (dict in the repo schema, or a Task).

    Used by the original one-function entry points (compute_reference_answer, check_render, needs_image).
    """
    from pathlib import Path as _P
    import os

    from generate.registry import discover
    from runner.config import Config
    from runner.schema import Task, from_official, task_fingerprint
    from validate import hand_check

    root = _P(__file__).resolve().parent.parent
    cfg = Config.load(root)
    if not isinstance(task, Task):
        rec = from_official(dict(task))
        rec = {k: v for k, v in rec.items() if k in Task.__dataclass_fields__}
        defaults = {"generator": "", "index": 0, "seed": 0, "visual_type": "", "question_type": "",
                    "answer_type": "text", "tolerance": None, "real_world_examples": [], "image_path": "",
                    "data_path": "", "question": "", "answer": None}
        task = Task(**{**defaults, **rec})
    if image_path is not None:
        p = _P(image_path)
        if not p.is_absolute() and (root / p).exists():
            p = root / p  # a repo-relative path works from any folder
        task.image_path = os.path.relpath(p.resolve(), root)
    ctx = CheckContext(cfg=cfg, task=task, generator=discover().generators.get(task.generator),
                       ledger=hand_check.load(cfg.tasks_dir), fingerprint=task_fingerprint(root, task))
    ctx.load_data()
    return ctx
