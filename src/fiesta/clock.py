"""The single source of "what time is it".

Every module asks this one instead of calling ``datetime.now()`` directly, so
tests can freeze time by patching one place.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


def now_utc() -> datetime:
    """Current time, timezone-aware, in UTC."""
    return datetime.now(UTC)


def today_in(tz_name: str) -> date:
    """The calendar date it is *right now* in the given timezone.

    This is what the bot means by "today": a birthday on the 16th is celebrated
    while it is the 16th in the workspace's timezone, regardless of the server's
    own clock.
    """
    return now_utc().astimezone(ZoneInfo(tz_name)).date()


def local_time_to_utc(day: date, hhmm: str, tz_name: str) -> datetime:
    """Turn a local time like ``"10:00"`` on ``day`` into a UTC instant."""
    hour, minute = (int(part) for part in hhmm.split(":"))
    local = datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(tz_name)
    )
    return local.astimezone(UTC)
