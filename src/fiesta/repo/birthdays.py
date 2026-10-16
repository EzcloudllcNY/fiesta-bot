"""Birthday storage and the date matching that goes with it."""

import calendar
from datetime import date

from sqlmodel import Session, col, delete, select

from fiesta import clock
from fiesta.models import Birthday, Member

FEB = 2
LEAP_DAY = 29


def match_keys(target: date) -> set[tuple[int, int]]:
    """The stored ``(month, day)`` pairs that count as falling on ``target``.

    Normally that is just the target's own month and day. The exception is 29
    February: in a non-leap year those birthdays are celebrated on the 28th, so
    asking about 28 February 2026 also matches everyone born on the 29th.

    The stored value is never rewritten — only the lookup shifts — so the
    birthday snaps back to the 29th in the next leap year.
    """
    keys = {(target.month, target.day)}
    is_leap = calendar.isleap(target.year)
    if target.month == FEB and target.day == 28 and not is_leap:
        keys.add((FEB, LEAP_DAY))
    return keys


def falling_on(session: Session, target: date) -> list[Birthday]:
    """Visible birthdays of active members that fall on ``target``.

    Hidden birthdays, bots and deactivated accounts are excluded here rather
    than at each call site, so no caller can forget to respect the privacy
    setting.
    """
    keys = match_keys(target)
    statement = (
        select(Birthday)
        .join(Member, col(Member.slack_user_id) == col(Birthday.member_id))
        .where(
            col(Birthday.is_hidden) == False,  # noqa: E712
            col(Member.is_bot) == False,  # noqa: E712
            col(Member.is_deleted) == False,  # noqa: E712
        )
    )
    # SQLite has no tuple-IN, and the candidate set is at most two pairs, so
    # filter in Python rather than building an OR chain.
    return [b for b in session.exec(statement) if (b.month, b.day) in keys]


def get(session: Session, member_id: str) -> Birthday | None:
    return session.get(Birthday, member_id)


def by_member(session: Session) -> dict[str, Birthday]:
    """Every stored birthday, hidden ones included, keyed by member."""
    return {b.member_id: b for b in session.exec(select(Birthday))}


def upsert(
    session: Session,
    *,
    member_id: str,
    month: int,
    day: int,
    year: int | None = None,
) -> Birthday:
    validate(month, day, year)
    birthday = session.get(Birthday, member_id)
    if birthday is None:
        birthday = Birthday(member_id=member_id, month=month, day=day)

    birthday.month = month
    birthday.day = day
    birthday.year = year
    birthday.updated_at = clock.now_utc()
    session.add(birthday)
    return birthday


def remove(session: Session, member_id: str) -> bool:
    """Delete a birthday outright. Returns whether there was one to delete."""
    birthday = session.get(Birthday, member_id)
    if birthday is None:
        return False
    session.exec(delete(Birthday).where(col(Birthday.member_id) == member_id))
    return True


def set_hidden(
    session: Session, member_id: str, hidden: bool
) -> Birthday | None:
    birthday = session.get(Birthday, member_id)
    if birthday is None:
        return None
    birthday.is_hidden = hidden
    birthday.updated_at = clock.now_utc()
    session.add(birthday)
    return birthday


def validate(month: int, day: int, year: int | None) -> None:
    """Reject impossible dates before they reach the database.

    29 February is accepted; the year is validated only loosely, since it is
    optional and never displayed.
    """
    if not 1 <= month <= 12:
        raise ValueError(f"Month must be between 1 and 12, got {month}")

    # Compare against a leap year so 29 February is allowed regardless of the
    # given year.
    max_day = calendar.monthrange(2024, month)[1]
    if not 1 <= day <= max_day:
        raise ValueError(
            f"Day must be between 1 and {max_day} for month {month}, got {day}"
        )

    if year is not None and not 1900 <= year <= date.today().year:
        raise ValueError(f"Year looks wrong: {year}")
