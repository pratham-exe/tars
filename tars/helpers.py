"""Shared helper functions for TARS UI."""

from __future__ import annotations

from datetime import datetime, timezone


def time_ago(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    now = datetime.now(timezone.utc)
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def format_started(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    if dt.date() == now.date():
        return f"today {dt.strftime('%H:%M')}"
    diff = (now.date() - dt.date()).days
    if diff == 1:
        return f"yesterday {dt.strftime('%H:%M')}"
    return dt.strftime("%b %d %H:%M")


def truncate(text: str, length: int = 60) -> str:
    text = text.replace("\n", " ").strip()
    return text[:length] + "…" if len(text) > length else text


def escape_markup(text: str) -> str:
    """Escape square brackets for rich markup."""
    return text.replace("[", "\\[").replace("]", "\\]")
