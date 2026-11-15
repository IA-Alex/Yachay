"""Disk admission policy (spec section 5, correction 16).

Before a job is claimed, require free space in `data/` to exceed the input
size times the admission factor. If the rule fails the job is deferred.
"""

from __future__ import annotations


def has_space(*, free_bytes: int, input_size: int, factor: float) -> bool:
    """Return True when free space covers `input_size * factor`."""
    return free_bytes >= input_size * factor
