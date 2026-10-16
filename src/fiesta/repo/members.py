"""Roster reads and writes."""

from datetime import datetime

from sqlmodel import Session, col, select

from fiesta import clock
from fiesta.models import Member, MemberPrefs


def get(session: Session, member_id: str) -> Member | None:
    return session.get(Member, member_id)


def upsert(
    session: Session,
    *,
    slack_user_id: str,
    display_name: str,
    real_name: str | None = None,
    tz: str | None = None,
    is_bot: bool = False,
    is_deleted: bool = False,
    joined_at: datetime | None = None,
) -> Member:
    """Store what Slack told us about a user; leave their preferences alone."""
    member = session.get(Member, slack_user_id)
    if member is None:
        member = Member(slack_user_id=slack_user_id, display_name=display_name)
        member.joined_at = joined_at or clock.now_utc()

    member.display_name = display_name
    member.real_name = real_name
    member.tz = tz
    member.is_bot = is_bot
    member.is_deleted = is_deleted
    member.updated_at = clock.now_utc()
    session.add(member)
    return member


def ensure(
    session: Session, slack_user_id: str, *, display_name: str
) -> Member:
    """Make sure a member row exists, without touching one that already does.

    Every table a member owns points at this one with a foreign key, and foreign
    keys are enforced, so a handler writing for someone the roster has never
    seen would fail with an IntegrityError. That happens whenever a `team_join`
    goes missing: the sweep that would have noticed only runs at startup and at
    03:00.

    The name is whatever the interaction payload carried; the next roster sync
    replaces it with the real one.
    """
    member = session.get(Member, slack_user_id)
    if member is not None:
        return member
    return upsert(
        session, slack_user_id=slack_user_id, display_name=display_name
    )


def mark_deleted(session: Session, member_ids: set[str]) -> int:
    """Flag users who no longer appear in the Slack roster.

    Returns how many changed.
    """
    if not member_ids:
        return 0
    changed = 0
    statement = select(Member).where(col(Member.slack_user_id).in_(member_ids))
    for member in session.exec(statement):
        if not member.is_deleted:
            member.is_deleted = True
            member.updated_at = clock.now_utc()
            session.add(member)
            changed += 1
    return changed


def active(session: Session) -> list[Member]:
    """Real people who can still be messaged."""
    statement = select(Member).where(
        col(Member.is_bot) == False,  # noqa: E712
        col(Member.is_deleted) == False,  # noqa: E712
    )
    return list(session.exec(statement))


def everyone(session: Session) -> list[Member]:
    """Every member the roster knows, bots and leavers too, by name."""
    statement = select(Member).order_by(
        col(Member.display_name).collate("NOCASE")
    )
    return list(session.exec(statement))


def all_ids(session: Session) -> set[str]:
    return set(session.exec(select(Member.slack_user_id)))


def get_prefs(session: Session, member_id: str) -> MemberPrefs:
    """Preferences for a member, created on first access with defaults."""
    prefs = session.get(MemberPrefs, member_id)
    if prefs is None:
        prefs = MemberPrefs(member_id=member_id)
        session.add(prefs)
    return prefs


def set_muted_upcoming(
    session: Session, member_id: str, muted: bool
) -> MemberPrefs:
    prefs = get_prefs(session, member_id)
    prefs.muted_upcoming = muted
    prefs.updated_at = clock.now_utc()
    session.add(prefs)
    return prefs


def muted_ids(session: Session) -> set[str]:
    """Members who asked not to hear about upcoming birthdays."""
    statement = select(MemberPrefs.member_id).where(
        col(MemberPrefs.muted_upcoming) == True  # noqa: E712
    )
    return set(session.exec(statement))
