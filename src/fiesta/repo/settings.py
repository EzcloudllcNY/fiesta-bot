"""Admin settings, read through defaults so a fresh database already works."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlmodel import Session, select

from fiesta import clock
from fiesta.models import Setting


class UnknownSetting(KeyError):
    """Raised instead of silently accepting a typo'd setting name."""


class InvalidValue(ValueError):
    """An admin typed a value the setting cannot use."""


def _channel_id(raw: str) -> str:
    # A name like "#general" would be accepted here and then fail on every post,
    # so the mistake is caught while the admin is still at the keyboard.
    if not re.fullmatch(r"[CG][A-Z0-9]{6,}", raw):
        raise InvalidValue(
            "expected a channel ID like C0123456789 "
            "(channel details → About), not a name"
        )
    return raw


def _clock_time(raw: str) -> str:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise InvalidValue("expected a 24-hour time like 09:30")
    return f"{int(match[1]):02d}:{match[2]}"


def _timezone(raw: str) -> str:
    try:
        ZoneInfo(raw)
    except ZoneInfoNotFoundError, ValueError:
        raise InvalidValue(
            "expected an IANA timezone like Europe/Kyiv"
        ) from None
    return raw


def _whole_number(minimum: int) -> Callable[[str], int]:
    def parse(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError:
            raise InvalidValue("expected a whole number") from None
        if value < minimum:
            raise InvalidValue(f"expected {minimum} or more")
        return value

    return parse


def _one_of(*choices: str) -> Callable[[str], str]:
    def parse(raw: str) -> str:
        if raw not in choices:
            raise InvalidValue(f"expected one of: {', '.join(choices)}")
        return raw

    return parse


def _switch(raw: str) -> bool:
    # The same spelling the database stores, so what `get` shows can be typed
    # straight back into `set`.
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise InvalidValue("expected true or false")


@dataclass(frozen=True, slots=True)
class Spec:
    default: Any
    description: str
    parse: Callable[[str], Any]
    needs_restart: bool = False


SPECS: dict[str, Spec] = {
    "celebration_channel": Spec(
        None,
        "channel ID for the birthday post; nothing is posted until set",
        _channel_id,
    ),
    "post_time": Spec(
        "10:00",
        "local time the daily job runs",
        _clock_time,
        needs_restart=True,
    ),
    "timezone": Spec(
        "Europe/Kyiv",
        "workspace timezone; 'today' means today here",
        _timezone,
        needs_restart=True,
    ),
    "ask_delay_days": Spec(
        3, "days after joining before the first ask", _whole_number(0)
    ),
    "max_asks": Spec(
        3, "first ask plus reminders, then it gives up", _whole_number(1)
    ),
    "ask_interval_days": Spec(4, "days between reminders", _whole_number(1)),
    "upcoming_days_before": Spec(
        3, "how many days ahead the heads-up DMs go out", _whole_number(1)
    ),
    "gif_provider": Spec(
        "none", "none, or db for the curated GIF pool", _one_of("none", "db")
    ),
    # Feature switches. Each is read on every run, so flipping one needs no
    # restart.
    "auto_send_enabled": Spec(
        True,
        "the bot delivers queued messages on its own; "
        "`fiesta send-due` still works when off",
        _switch,
    ),
    "celebration_enabled": Spec(
        True, "the birthday post in the celebration channel", _switch
    ),
    "upcoming_enabled": Spec(
        True, "the heads-up DMs before a birthday", _switch
    ),
    "asks_enabled": Spec(
        True, "asking members for their birthday, and reminders", _switch
    ),
}

DEFAULTS: dict[str, Any] = {key: spec.default for key, spec in SPECS.items()}


def spec_for(key: str) -> Spec:
    try:
        return SPECS[key]
    except KeyError:
        raise UnknownSetting(key) from None


def parse(key: str, raw: str) -> Any:
    """Turn admin input into a value to store, or raise ``InvalidValue``."""
    return spec_for(key).parse(raw.strip())


def get(session: Session, key: str) -> Any:
    spec = spec_for(key)
    row = session.get(Setting, key)
    return json.loads(row.value) if row else spec.default


def set_value(session: Session, key: str, value: Any) -> None:
    spec_for(key)
    encoded = json.dumps(value)
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=encoded))
    else:
        row.value = encoded
        row.updated_at = clock.now_utc()
        session.add(row)


def reset(session: Session, key: str) -> bool:
    """Drop the stored value so the default applies again.

    Returns whether one existed.
    """
    spec_for(key)
    row = session.get(Setting, key)
    if row is None:
        return False
    session.delete(row)
    return True


def stored_values(session: Session) -> dict[str, Any]:
    """Only the settings an admin has changed."""
    rows = session.exec(select(Setting))
    # A row left behind by a setting that no longer exists is not worth showing
    # anyone.
    return {row.key: json.loads(row.value) for row in rows if row.key in SPECS}


def all_values(session: Session) -> dict[str, Any]:
    """Every setting, with stored values overriding the defaults."""
    return DEFAULTS | stored_values(session)
