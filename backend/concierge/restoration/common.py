"""Pieces shared by the v1 and v2 engines."""
from __future__ import annotations


class RestorationError(ValueError):
    """Bad scenario input or a labor sheet that cannot price a repair. The message is safe to show to a caller."""


def display(lo: int, hi: int) -> str:
    """Plain-language range. Switches to days for long outages."""
    if hi < 36:
        if lo == hi:
            return f"about {hi} hour{'s' if hi != 1 else ''}"
        return f"about {lo} to {hi} hours"
    lo_d, hi_d = max(1, round(lo / 24)), max(1, round(hi / 24))
    if lo_d == hi_d:
        return f"about {hi_d} day{'s' if hi_d != 1 else ''}"
    return f"about {lo_d} to {hi_d} days"
