"""Jinja filters used by the ranking and guess form templates."""

import math
from datetime import datetime, timedelta

from app.core.config import get_settings
from app.core.templates import templates


def rank_to_color(rank: int, total: int) -> str:
    """Pastel colour from green (rank 1) to red (last), on a log scale (PRD RF-3)."""
    t = 0.0 if rank <= 1 or total <= 1 else min(1.0, math.log(rank) / math.log(total))
    hue = round(120 * (1 - t))
    return f"hsl({hue}, 55%, 82%)"


def local_datetime(moment: datetime) -> str:
    """e.g. '14/03 às 18:30' in the game's time zone."""
    return moment.astimezone(get_settings().tz).strftime("%d/%m às %H:%M")


def duration(delta: timedelta) -> str:
    """e.g. '5h 12min' or '8min'."""
    minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}min" if hours else f"{minutes}min"


templates.env.filters["rank_color"] = rank_to_color
templates.env.filters["local_datetime"] = local_datetime
templates.env.filters["duration"] = duration
