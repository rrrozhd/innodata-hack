"""Finds generators in generate/generators/ and checks they are filled in correctly."""
from __future__ import annotations

import importlib
import pkgutil
import re
import traceback
from pathlib import Path

from runner.schema import ANSWER_TYPES
from generate.base import TaskGenerator

REQUIRED_METHODS = ("generate_data", "render", "compute_answer", "make_question")
GEN_PACKAGE = "generate.generators"
GEN_DIR = Path(__file__).parent / "generators"


class Registry:
    def __init__(self):
        self.generators: dict[str, TaskGenerator] = {}
        self.disabled: list[str] = []
        self.problems: list[str] = []  # human-readable reasons something was rejected

    def add(self, cls_or_obj, source: str = "") -> None:
        gen = cls_or_obj() if isinstance(cls_or_obj, type) else cls_or_obj
        label = f"{source or type(gen).__module__}:{type(gen).__name__}"
        issues = []
        if not re.fullmatch(r"[a-z][a-z0-9_]*", gen.name or ""):
            issues.append("`name` must be snake_case, e.g. 'bus_timetable'")
        if not gen.visual_type:
            issues.append("`visual_type` is empty")
        if not gen.question_type:
            issues.append("`question_type` is empty")
        if gen.answer_type not in ANSWER_TYPES:
            issues.append(f"`answer_type` must be one of {ANSWER_TYPES}")
        examples = gen.real_world_examples
        if not (isinstance(examples, list) and 1 <= len(examples) <= 2
                and all(isinstance(e, str) and e.strip() for e in examples)):
            issues.append("`real_world_examples` must list 1-2 non-empty strings (the rules ask "
                          "you to cite one or two real examples behind each image)")
        if gen.tolerance is not None and (not isinstance(gen.tolerance, (int, float)) or gen.tolerance < 0):
            issues.append("`tolerance` must be None or a non-negative number")
        if gen.fixed_question and gen.question_has_placeholders():
            issues.append("`fixed_question = True` means every task asks the identical sentence, but "
                          "`question_template` has {placeholders}; remove them or set fixed_question = False")
        if gen.source_key is not None and not (isinstance(gen.source_key, str) and gen.source_key):
            issues.append("`source_key` must be None or the name of the data field that holds the source code")
        if not re.fullmatch(r"[a-z0-9]+", gen.source_ext or ""):
            issues.append("`source_ext` must be lowercase letters/digits, e.g. 'mmd'")
        if gen.name in self.generators:
            issues.append(f"duplicate generator name {gen.name!r}")
        if not gen.enabled:
            self.disabled.append(gen.name or label)
            return
        impl = gen.implemented()
        missing = [m for m in REQUIRED_METHODS if not impl[m]]
        if missing:
            issues.append("required method(s) not implemented yet: " + ", ".join(missing)
                          + "  (set `enabled = False` while the generator is unfinished)")
        if issues:
            self.problems.append(f"{label}: " + "; ".join(issues))
            return
        self.generators[gen.name] = gen


def discover(extra: list | None = None, include_user: bool = True) -> Registry:
    """Import every non-underscore module in generate/generators/ and register its generators."""
    reg = Registry()
    if include_user:
        pkg = importlib.import_module(GEN_PACKAGE)
        for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
            if info.name.startswith("_"):
                continue
            modname = f"{GEN_PACKAGE}.{info.name}"
            try:
                module = importlib.import_module(modname)
            except Exception:  # a teammate's syntax error must be loud, not silent
                tail = traceback.format_exc().strip().splitlines()[-3:]
                reg.problems.append(f"{modname}: failed to import -> " + " | ".join(t.strip() for t in tail))
                continue
            found = [
                obj for obj in vars(module).values()
                if isinstance(obj, type) and issubclass(obj, TaskGenerator)
                and obj is not TaskGenerator and obj.__module__ == modname
            ]
            if not found:
                reg.problems.append(f"{modname}: no TaskGenerator subclass found in this file")
            for cls in found:
                reg.add(cls, source=modname)
    for cls in extra or []:
        reg.add(cls, source="(built-in)")
    return reg
