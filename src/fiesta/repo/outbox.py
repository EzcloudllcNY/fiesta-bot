"""The outbound queue: enqueue, claim, and record the result."""

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, col, select

from fiesta import clock
from fiesta.models import MessageKind, OutboxMessage, OutboxStatus

MAX_ATTEMPTS = 5
"""How many times a message is retried before it is given up on."""


def enqueue(
    session: Session,
    *,
    kind: MessageKind,
    channel: str,
    blocks: list[dict[str, Any]],
    fallback_text: str,
    dedupe_key: str,
    scheduled_for: datetime | None = None,
) -> bool:
    """Queue a message, ignoring it if that ``dedupe_key`` was already queued.

    Returns whether a new row was actually created. The ON CONFLICT DO NOTHING
    is the whole idempotency story: jobs can be re-run after a crash, replayed
    by the startup catch-up, or triggered by hand, and the duplicate simply
    collides and vanishes.
    """
    statement = (
        # SQLModel's metaclass builds __table__; no stub describes that, hence
        # the ignore.
        sqlite_insert(OutboxMessage.__table__)  # pyright: ignore[reportAttributeAccessIssue]
        .values(
            kind=kind,
            channel=channel,
            blocks_json=json.dumps(blocks),
            fallback_text=fallback_text,
            dedupe_key=dedupe_key,
            status=OutboxStatus.PENDING,
            attempts=0,
            scheduled_for=scheduled_for or clock.now_utc(),
            created_at=clock.now_utc(),
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )
    return session.exec(statement).rowcount > 0


def due(
    session: Session, now: datetime | None = None, *, limit: int = 50
) -> list[OutboxMessage]:
    """The next batch of messages ready to send, oldest first."""
    moment = now or clock.now_utc()
    statement = (
        select(OutboxMessage)
        .where(
            col(OutboxMessage.status) == OutboxStatus.PENDING,
            col(OutboxMessage.scheduled_for) <= moment,
        )
        .order_by(col(OutboxMessage.scheduled_for), col(OutboxMessage.id))
        .limit(limit)
    )
    return list(session.exec(statement))


def mark_sent(
    session: Session, message: OutboxMessage, *, slack_ts: str | None
) -> None:
    message.status = OutboxStatus.SENT
    message.sent_at = clock.now_utc()
    message.slack_ts = slack_ts
    message.last_error = None
    session.add(message)


def mark_skipped(
    session: Session, message: OutboxMessage, *, reason: str
) -> None:
    """Give up immediately on an error that retrying cannot fix.

    A deleted channel or a user id that Slack does not recognise will fail
    identically forever, so burning five retries on it only delays the rest of
    the queue.
    """
    message.status = OutboxStatus.SKIPPED
    message.last_error = reason
    session.add(message)


def mark_failed_attempt(
    session: Session, message: OutboxMessage, *, error: str
) -> None:
    """Record a transient failure and back off, or give up when out of tries."""
    message.attempts += 1
    message.last_error = error

    if message.attempts >= MAX_ATTEMPTS:
        message.status = OutboxStatus.FAILED
    else:
        message.scheduled_for = clock.now_utc() + timedelta(
            minutes=2**message.attempts
        )

    session.add(message)


def by_dedupe_key(session: Session, dedupe_key: str) -> OutboxMessage | None:
    statement = select(OutboxMessage).where(
        col(OutboxMessage.dedupe_key) == dedupe_key
    )
    return session.exec(statement).first()
