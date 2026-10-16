"""Builders for test data.

Every one takes sensible defaults so a test only has to state the part that
matters to it. A test about muting should read as "a member who muted", not as
eight keyword arguments about bots and timezones.
"""

from datetime import date

from sqlmodel import Session

from fiesta.models import Birthday, Member, MemberPrefs
from fiesta.repo import settings as settings_repo


def add_member(
    session: Session,
    user_id: str,
    *,
    name: str | None = None,
    is_bot: bool = False,
    is_deleted: bool = False,
    muted: bool = False,
) -> Member:
    member = Member(
        slack_user_id=user_id,
        display_name=name or f"user-{user_id}",
        is_bot=is_bot,
        is_deleted=is_deleted,
    )
    session.add(member)

    if muted:
        session.add(MemberPrefs(member_id=user_id, muted_upcoming=True))

    session.commit()
    return member


def add_team(session: Session, *user_ids: str) -> list[Member]:
    """Several ordinary members at once."""
    return [add_member(session, user_id) for user_id in user_ids]


def add_birthday(
    session: Session,
    user_id: str,
    on: date | tuple[int, int],
    *,
    hidden: bool = False,
    year: int | None = None,
) -> Birthday:
    """Give a member a birthday, as a date or a (month, day) pair."""
    month, day = (on.month, on.day) if isinstance(on, date) else on

    birthday = Birthday(
        member_id=user_id,
        month=month,
        day=day,
        year=year,
        is_hidden=hidden,
    )
    session.add(birthday)
    session.commit()
    return birthday


def configure(session: Session, **settings) -> None:
    """Set several admin settings in one line."""
    for key, value in settings.items():
        settings_repo.set_value(session, key, value)
    session.commit()
