"""Reads and writes for the birthday-ask state machine.

    pending --first ask--> asked --+-- modal submit --> answered
                                   +-- "never ask" ---> declined
                                   +-- out of asks ---> exhausted

The three end states are terminal for the *bot* — it stops reaching out — but a
member can always change their mind through /birthday.
"""

from datetime import datetime, timedelta

from sqlmodel import Session, col, select

from fiesta import clock
from fiesta.models import AskState, AskStatus


def get(session: Session, member_id: str) -> AskState | None:
    return session.get(AskState, member_id)


def create_pending(
    session: Session, member_id: str, *, delay_days: int
) -> AskState:
    """Queue a member to be asked once the post-join delay has passed.

    Existing state is left alone, so a roster sync can call this for everyone
    without resurrecting someone who already answered or opted out.
    """
    existing = session.get(AskState, member_id)
    if existing is not None:
        return existing

    state = AskState(
        member_id=member_id,
        status=AskStatus.PENDING,
        next_ask_at=clock.now_utc() + timedelta(days=delay_days),
    )
    session.add(state)
    return state


def due(
    session: Session, now: datetime | None = None, *, max_asks: int
) -> list[AskState]:
    """Members who are waiting to be asked and whose wait is over."""
    moment = now or clock.now_utc()
    statement = select(AskState).where(
        col(AskState.status).in_([AskStatus.PENDING, AskStatus.ASKED]),
        col(AskState.next_ask_at).is_not(None),
        col(AskState.next_ask_at) <= moment,
        col(AskState.asks_sent) < max_asks,
    )
    return list(session.exec(statement))


def record_ask(
    session: Session,
    state: AskState,
    *,
    max_asks: int,
    interval_days: int,
) -> AskState:
    """Count an ask that was just queued and schedule the next one.

    Once the final ask goes out the member moves to ``exhausted`` and
    ``next_ask_at`` is cleared, which is what takes them out of the ``due``
    query for good.
    """
    state.asks_sent += 1
    state.last_asked_at = clock.now_utc()
    state.updated_at = clock.now_utc()

    if state.asks_sent >= max_asks:
        state.status = AskStatus.EXHAUSTED
        state.next_ask_at = None
    else:
        state.status = AskStatus.ASKED
        state.next_ask_at = clock.now_utc() + timedelta(days=interval_days)

    session.add(state)
    return state


def mark_answered(session: Session, member_id: str) -> AskState:
    return _settle(session, member_id, AskStatus.ANSWERED)


def mark_declined(session: Session, member_id: str) -> AskState:
    return _settle(session, member_id, AskStatus.DECLINED)


def _settle(session: Session, member_id: str, status: AskStatus) -> AskState:
    """Move a member to a terminal state, creating the row if it is missing.

    The row can be absent when a member runs /birthday before the bot ever got
    around to asking them.
    """
    state = session.get(AskState, member_id)
    if state is None:
        state = AskState(member_id=member_id)

    state.status = status
    state.next_ask_at = None
    state.updated_at = clock.now_utc()
    session.add(state)
    return state
