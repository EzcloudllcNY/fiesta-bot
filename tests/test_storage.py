"""Timezone handling at the database boundary."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import StatementError

from factories import add_member
from fiesta import clock
from fiesta.models import MessageKind, OutboxMessage
from fiesta.repo import asks as asks_repo
from fiesta.repo import outbox as outbox_repo


class TestDatetimesStayAware:
    """SQLite drops the offset on write, so values must be re-tagged on read.

    Without that, a datetime loaded from the database is naive while a fresh one
    is aware, and the two blow up the first time they meet in arithmetic —
    usually far from the code that caused it.
    """

    def test_a_datetime_read_back_is_still_timezone_aware(self, session):
        add_member(session, "U_ANA")
        asks_repo.create_pending(session, "U_ANA", delay_days=3)
        session.commit()
        session.expire_all()  # force a real read rather than the identity map

        loaded = asks_repo.get(session, "U_ANA")

        assert loaded.next_ask_at.tzinfo is not None

    def test_a_loaded_datetime_can_be_compared_with_a_fresh_one(self, session):
        add_member(session, "U_ANA")
        asks_repo.create_pending(session, "U_ANA", delay_days=3)
        session.commit()
        session.expire_all()

        loaded = asks_repo.get(session, "U_ANA")

        assert loaded.next_ask_at > clock.now_utc()  # would TypeError if naive

    def test_the_stored_instant_is_preserved_exactly(self, session):
        add_member(session, "U_ANA")
        asks_repo.create_pending(session, "U_ANA", delay_days=3)
        session.commit()
        expected = clock.now_utc() + timedelta(days=3)
        session.expire_all()

        loaded = asks_repo.get(session, "U_ANA")

        assert loaded.next_ask_at == expected

    def test_a_non_utc_datetime_is_normalised_rather_than_stored_as_written(
        self, session
    ):
        """Two identical instants compare equal however they were written."""
        # An instant that has already passed, expressed in a non-UTC zone.
        instant = datetime(2026, 3, 10, 8, 0, tzinfo=UTC)
        as_kyiv_time = instant.astimezone(ZoneInfo("Europe/Kyiv"))
        outbox_repo.enqueue(
            session,
            kind=MessageKind.ASK,
            channel="U_ANA",
            blocks=[],
            fallback_text="hi",
            dedupe_key="tz-test",
            scheduled_for=as_kyiv_time,
        )
        session.commit()

        (message,) = outbox_repo.due(session)
        assert message.scheduled_for == instant

    def test_storing_a_naive_datetime_is_refused(self, session):
        """A naive value has no defined meaning, so the boundary rejects it."""
        session.add(
            OutboxMessage(
                kind=MessageKind.ASK,
                channel="U_ANA",
                blocks_json="[]",
                fallback_text="hi",
                dedupe_key="naive",
                scheduled_for=datetime(2026, 3, 10, 9, 0),  # no tzinfo
            )
        )

        # SQLAlchemy wraps the type's own error, so the check is on the message.
        with pytest.raises(StatementError, match="naive datetime"):
            session.commit()
