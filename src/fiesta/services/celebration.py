"""The birthday-day post in the celebration channel."""

import logging
from datetime import date
from enum import StrEnum

from sqlmodel import Session

from fiesta.models import MessageKind
from fiesta.repo import birthdays as birthdays_repo
from fiesta.repo import gifs as gifs_repo
from fiesta.repo import outbox as outbox_repo
from fiesta.repo import settings as settings_repo
from fiesta.services import templates as templates_service
from fiesta.slack import blocks

log = logging.getLogger(__name__)


class Outcome(StrEnum):
    """Why a day did or did not produce a post.

    NOT_CONFIGURED is kept apart from NOTHING_TO_POST because only one of them
    means the day was handled. A day with no birthdays is finished; a day
    skipped for want of a channel is not, and must stay replayable once someone
    sets one.

    DISABLED is a finished day. An admin who switched posts off meant it, and
    switching them back on should not flush a week of late birthdays into the
    channel.
    """

    QUEUED = "queued"
    ALREADY_QUEUED = "already_queued"  # a re-run of a day already handled
    NOTHING_TO_POST = "nothing_to_post"
    NOT_CONFIGURED = "not_configured"
    DISABLED = "disabled"


def enqueue_for(session: Session, today: date) -> Outcome:
    """Queue today's celebration posts, one per birthday person.

    Each person gets a post of their own, with a text written for one.
    """
    if not settings_repo.get(session, "celebration_enabled"):
        log.info(
            "Celebration posts are switched off — not posting for %s", today
        )
        return Outcome.DISABLED

    channel = settings_repo.get(session, "celebration_channel")
    if not channel:
        log.warning(
            "No celebration_channel configured — not posting for %s. "
            "Set one with `fiesta settings set celebration_channel C...` "
            "and the day will be picked up.",
            today,
        )
        return Outcome.NOT_CONFIGURED

    celebrants = birthdays_repo.falling_on(session, today)
    if not celebrants:
        return Outcome.NOTHING_TO_POST

    queued = 0
    for user_id in sorted(b.member_id for b in celebrants):
        queued += int(
            outbox_repo.enqueue(
                session,
                kind=MessageKind.CELEBRATION,
                channel=channel,
                blocks=blocks.celebration_blocks(
                    text=templates_service.render(session, user_id),
                    gif_url=_pick_gif_url(session),
                ),
                fallback_text="Happy birthday!",
                dedupe_key=f"celebration:{today}:{user_id}",
            )
        )
    return Outcome.QUEUED if queued else Outcome.ALREADY_QUEUED


def _pick_gif_url(session: Session) -> str | None:
    """A GIF, if GIFs are switched on and the pool has one.

    Phase 1 always returns None.
    """
    if settings_repo.get(session, "gif_provider") != "db":
        return None

    gif = gifs_repo.pick(session)
    return gif.url if gif else None
