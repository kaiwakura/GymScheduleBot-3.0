"""Small text helpers shared by the planner, the views, sync and the coach."""

from __future__ import annotations

from datetime import datetime


def clock(moment: datetime) -> str:
    """7:05 PM, without a leading zero (strftime's %-I doesn't work on Windows)."""
    return moment.strftime("%I:%M %p").lstrip("0")


def join_words(items: list[str]) -> str:
    """["a"] -> "a", ["a", "b", "c"] -> "a, b and c"."""
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
