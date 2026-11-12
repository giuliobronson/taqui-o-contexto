from datetime import UTC, date, datetime

from app.core.config import get_settings


def utcnow() -> datetime:
    """Current time. FastAPI routes depend on this so tests can override it."""
    return datetime.now(UTC)


def local_date(moment: datetime) -> date:
    """Calendar day of `moment` in the game's time zone (the one-guess-per-day unit)."""
    return moment.astimezone(get_settings().tz).date()
