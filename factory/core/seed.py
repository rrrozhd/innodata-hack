"""Seeded randomness. Every stream is derived from a hash of its name, so adding a
stream never shifts another one, and the same seed always gives the same task."""
from __future__ import annotations

import hashlib

import numpy as np


class Reject(Exception):
    """Raised by a generator when a sampled scene cannot be made valid; the build
    loop moves to the next attempt of the same seed."""


def derive_seed(*parts) -> int:
    key = "/".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")


def rng_for(*parts) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(derive_seed(*parts)))


def pick(rng: np.random.Generator, seq):
    return seq[int(rng.integers(len(seq)))]


def randint(rng: np.random.Generator, lo: int, hi: int) -> int:
    """Inclusive on both ends."""
    return int(rng.integers(lo, hi + 1))


def shuffled(rng: np.random.Generator, seq) -> list:
    return [seq[int(i)] for i in rng.permutation(len(seq))]
