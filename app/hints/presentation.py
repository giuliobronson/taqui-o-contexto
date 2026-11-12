"""Jinja filters used by the hint board."""

from datetime import datetime

from app.core.templates import templates
from app.guesses.presentation import local_datetime


def time_ago(moment: datetime, now: datetime) -> str:
    """e.g. 'agora', 'há 5 min', 'há 3 h', 'há 2 dias'."""
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 60:
        return "agora"
    minutes = seconds // 60
    if minutes < 60:
        return f"há {minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"há {hours} h"
    days = hours // 24
    if days < 7:
        return f"há {days} dia" if days == 1 else f"há {days} dias"
    return local_datetime(moment)


templates.env.filters["time_ago"] = time_ago
