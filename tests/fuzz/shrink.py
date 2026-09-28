"""Failure minimiser for the silent-CLEAN fuzz harness.

Given a deterministic builder and a predicate that says a case still fails,
greedily drop operators while the failure survives. A minimal reproducer is
then ``(seed, remaining operators)``.
"""

from __future__ import annotations

from typing import Callable, Iterable

#: The builder takes a tuple of operator names and returns something the
#: predicate can judge (or ``None`` if the case could not be built).
Builder = Callable[[tuple[str, ...]], object]
Predicate = Callable[[object], bool]


def shrink(
    operators: Iterable[str],
    build: Builder,
    is_failure: Predicate,
    *,
    max_passes: int = 4,
) -> tuple[str, ...]:
    """Return the smallest operator set (by greedy removal) that still fails."""
    current = tuple(operators)
    for _ in range(max_passes):
        changed = False
        index = 0
        while index < len(current):
            candidate = current[:index] + current[index + 1:]
            built = build(candidate)
            if built is not None and is_failure(built):
                current = candidate
                changed = True
            else:
                index += 1
        if not changed:
            break
    return current
