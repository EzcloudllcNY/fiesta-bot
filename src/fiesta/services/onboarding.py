"""Asking members for their birthday, and reminding them a few times."""

import logging

from sqlmodel import Session

from fiesta.models import TERMINAL_STATUSES, Member, MessageKind
from fiesta.repo import asks as asks_repo
from fiesta.repo import birthdays as birthdays_repo
from fiesta.repo import members as members_repo
from fiesta.repo import outbox as outbox_repo
from fiesta.repo import settings as settings_repo
from fiesta.slack import blocks
from fiesta.slack.blocks import AskTone

log = logging.getLogger(__name__)


def enqueue_due_asks(session: Session) -> int:
    """Queue an ask for every member whose wait is up.

    Returns how many were queued.

    This only writes rows — the sender delivers them — so it is cheap to run
    often and harmless to run twice.

    With asks switched off nobody's state moves, so switching them back on picks
    up where each member left off rather than skipping anyone's turn.
    """
    if not settings_repo.get(session, "asks_enabled"):
        return 0

    max_asks = settings_repo.get(session, "max_asks")

    queued = 0
    for state in asks_repo.due(session, max_asks=max_asks):
        tone = AskTone.REMINDER if state.asks_sent > 0 else AskTone.WELCOME
        queued += int(_ask(session, state.member_id, tone=tone))

    log.info("Queued %d birthday asks", queued)
    return queued


def introduction_recipients(session: Session) -> list[Member]:
    """Everyone who should hear that the bot has arrived.

    Anyone who already gave a birthday, answered, or asked not to be asked is
    left out, so the rollout can be re-run — or run against a workspace the bot
    has been in for a while — without pestering people who have already settled
    the question.

    So is anyone the bot has already asked even once. The introduction stands in
    for the *first* ask and nothing else; running the command twice must not
    hand out an extra reminder ahead of its schedule, and a member mid-way
    through their reminders should carry on through them rather than start
    again.
    """
    recipients = []
    for member in members_repo.active(session):
        if birthdays_repo.get(session, member.slack_user_id) is not None:
            continue

        state = asks_repo.get(session, member.slack_user_id)
        if state is not None and (
            state.asks_sent > 0 or state.status in TERMINAL_STATUSES
        ):
            continue

        recipients.append(member)
    return recipients


def enqueue_introductions(session: Session) -> int:
    """The one-off rollout DM: the bot is new, please tell it your birthday.

    This *is* the first ask rather than a message alongside it. It writes the
    same ``ask:{member}:{n}`` key and advances the same state machine, so nobody
    receives both this and the new-joiner welcome, and the ordinary reminders
    follow on from here.
    """
    queued = 0
    for member in introduction_recipients(session):
        queued += int(
            _ask(session, member.slack_user_id, tone=AskTone.INTRODUCTION)
        )

    log.info("Queued %d introductions", queued)
    return queued


def _ask(session: Session, member_id: str, *, tone: AskTone) -> bool:
    """Queue one ask and advance the member's state.

    Returns whether a row was created.
    """
    max_asks = settings_repo.get(session, "max_asks")
    interval_days = settings_repo.get(session, "ask_interval_days")

    state = asks_repo.create_pending(session, member_id, delay_days=0)
    attempt_no = state.asks_sent + 1
    if attempt_no > max_asks:
        return False

    created = outbox_repo.enqueue(
        session,
        kind=MessageKind.REMIND
        if tone is AskTone.REMINDER
        else MessageKind.ASK,
        channel=member_id,
        blocks=blocks.ask_blocks(tone=tone, asks_left=max_asks - attempt_no),
        fallback_text="When is your birthday?",
        dedupe_key=f"ask:{member_id}:{attempt_no}",
    )

    # The state advances either way. If the row was deduplicated this ask was
    # already queued on an earlier run, so counting it again would ask one extra
    # time.
    asks_repo.record_ask(
        session, state, max_asks=max_asks, interval_days=interval_days
    )
    return created
