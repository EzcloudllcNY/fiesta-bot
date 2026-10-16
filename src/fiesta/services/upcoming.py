"""The private heads-up that goes out a few days before someone's birthday."""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from sqlmodel import Session

from fiesta.models import MessageKind
from fiesta.repo import birthdays as birthdays_repo
from fiesta.repo import members as members_repo
from fiesta.repo import outbox as outbox_repo
from fiesta.repo import settings as settings_repo
from fiesta.slack import blocks

log = logging.getLogger(__name__)


class Outcome(StrEnum):
    """Why a day did or did not produce heads-ups.

    Kept apart from the count so "0 queued" can say whether the feature is off,
    nobody has a birthday that day, or the DMs went out on an earlier run.
    """

    QUEUED = "queued"
    ALREADY_QUEUED = "already_queued"  # a re-run of a day already handled
    NOTHING_TO_SEND = "nothing_to_send"  # no birthday on the target day
    NO_RECIPIENTS = "no_recipients"  # a birthday, but everyone else muted
    PASSED = "passed"  # catch-up only: the birthday is no longer ahead
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class Result:
    outcome: Outcome
    target: date  # the birthday date the heads-ups are about
    queued: int = 0


def enqueue_for(
    session: Session, day: date, *, today: date | None = None
) -> Result:
    """Queue heads-up DMs for birthdays ``upcoming_days_before`` after ``day``.

    Everyone active gets one except the birthday person themselves and anyone
    who muted these. In a thirty-person workspace that is about twenty-nine rows
    per birthday, which the sender paces out over roughly half a minute.

    ``today`` is passed by the catch-up, where ``day`` is a date the bot slept
    through and the lead time has already partly or wholly run out. Without it
    the heads-up is dated from ``day`` itself, which is what a live tick and
    ``fiesta tick --date`` both want.
    """
    days_before = settings_repo.get(session, "upcoming_days_before")
    target = day + timedelta(days=days_before)

    if not settings_repo.get(session, "upcoming_enabled"):
        return Result(Outcome.DISABLED, target)

    # A heads-up for a birthday that has already been and gone is worse than
    # silence: it tells the team to get ready for something they missed.
    days_away = days_before if today is None else (target - today).days
    if days_away <= 0:
        log.info(
            "Skipping a heads-up for %s — that birthday is no longer ahead",
            target,
        )
        return Result(Outcome.PASSED, target)

    celebrants = birthdays_repo.falling_on(session, target)
    if not celebrants:
        return Result(Outcome.NOTHING_TO_SEND, target)

    muted = members_repo.muted_ids(session)
    recipients = [
        m for m in members_repo.active(session) if m.slack_user_id not in muted
    ]

    queued = 0
    duplicates = 0
    for birthday in celebrants:
        for member in recipients:
            if member.slack_user_id == birthday.member_id:
                continue  # never tell people about their own birthday

            created = outbox_repo.enqueue(
                session,
                kind=MessageKind.UPCOMING,
                channel=member.slack_user_id,
                blocks=blocks.upcoming_blocks(
                    birthday_user_id=birthday.member_id,
                    days_away=days_away,
                    month=birthday.month,
                    day=birthday.day,
                ),
                fallback_text="A teammate's birthday is coming up",
                dedupe_key=f"upcoming:{birthday.member_id}:{member.slack_user_id}:{target}",
            )
            queued += int(created)
            duplicates += int(not created)

    log.info("Queued %d upcoming-birthday DMs for %s", queued, target)
    if queued:
        return Result(Outcome.QUEUED, target, queued)
    if duplicates:
        return Result(Outcome.ALREADY_QUEUED, target)
    return Result(Outcome.NO_RECIPIENTS, target)
