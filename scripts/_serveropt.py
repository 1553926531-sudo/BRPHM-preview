"""Small dependency-free map helper used by the simulation loader."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def run_map(func: Callable[[T], R], args: Iterable[T], n_workers: int, initializer=None) -> list[R]:
    if initializer is not None:
        initializer()
    return [func(item) for item in args]
