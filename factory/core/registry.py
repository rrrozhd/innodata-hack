"""Family and program registry. A new family is added here and nowhere else."""
from __future__ import annotations


def families() -> dict:
    from ..families import traingraph
    return {"traingraph": traingraph}


def programs() -> dict:
    out = {}
    for fam in families().values():
        out.update(fam.PROGRAMS)
    return out


def program(name: str):
    try:
        return programs()[name]
    except KeyError:
        raise SystemExit(f"unknown program {name!r}; available: {', '.join(sorted(programs()))}")


def family_of(scene_or_name) -> object:
    name = scene_or_name["family"] if isinstance(scene_or_name, dict) else scene_or_name
    return families()[name]


def render(scene: dict):
    return family_of(scene).render(scene)
