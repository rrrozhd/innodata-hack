"""Create a new generator file from the template:  python -m generate.new_generator my_chart_name"""
from __future__ import annotations

import re
import sys
from pathlib import Path

GEN_DIR = Path(__file__).resolve().parent / "generators"


def create(name: str) -> Path:
    """Copy _template.py to generators/<name>.py (never overwrites). Raises ValueError with a readable message."""
    name = name.strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError("name must be snake_case: lowercase letters, digits, underscores, starting with a letter")
    dest = GEN_DIR / f"{name}.py"
    if dest.exists():
        raise ValueError(f"{dest.name} already exists; not overwriting")
    text = (GEN_DIR / "_template.py").read_text(encoding="utf-8")
    camel = "".join(part.capitalize() for part in name.split("_")) + "Generator"
    text = text.replace("class MyGenerator", f"class {camel}").replace('name = "my_generator"', f'name = "{name}"')
    dest.write_text(text, encoding="utf-8")
    return dest


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m generate.new_generator my_chart_name")
        return 2
    try:
        dest = create(args[0])
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        return 1
    print(f"[PASS] created generate/generators/{dest.name}")
    print("       Next: open it, fill in the TODOs, set `enabled = True`, then run: python -m generate.generate_tasks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
