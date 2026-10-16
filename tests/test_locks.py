"""Only one process may drain the outbox.

The dedupe key stops a message being queued twice. Nothing about it stops two
*senders* picking up the same queued row, so this is the piece that keeps
`fiesta send-due` from double-posting over a running bot.
"""

import pytest
from sqlmodel import Session

import fiesta.db
from conftest import FakeSlack
from fiesta.db import create_all, make_engine
from fiesta.locks import drain_lock, drain_lock_path
from fiesta.models import MessageKind
from fiesta.repo import outbox as outbox_repo
from fiesta.slack import sender


@pytest.fixture
def file_session(tmp_path, monkeypatch):
    """A database on disk: a lock between processes needs a real file."""
    engine = make_engine(f"sqlite:///{tmp_path / 'fiesta.db'}")
    create_all(engine)
    monkeypatch.setattr(fiesta.db, "_engine", engine)

    with Session(engine) as session:
        yield session

    engine.dispose()


def queue_one(session) -> None:
    outbox_repo.enqueue(
        session,
        kind=MessageKind.CELEBRATION,
        channel="C_PARTY",
        blocks=[{"type": "section"}],
        fallback_text="Happy birthday!",
        dedupe_key="celebration:2026-03-20",
    )
    session.commit()


class TestTheLock:
    def test_the_second_holder_is_turned_away(self, file_session):
        with drain_lock() as first, drain_lock() as second:
            assert (first, second) == (True, False)

    def test_the_lock_is_released_at_the_end_of_the_block(self, file_session):
        with drain_lock():
            pass

        with drain_lock() as acquired:
            assert acquired

    def test_it_lives_beside_the_database(self, file_session, tmp_path):
        assert drain_lock_path() == tmp_path / "fiesta.db.drain.lock"

    def test_an_in_memory_database_needs_no_lock(self, session):
        """Nobody else can open it, or every test would fight over one file."""
        assert drain_lock_path() is None

        with drain_lock() as acquired:
            assert acquired


class TestDraining:
    def test_a_second_drainer_sends_nothing(self, file_session):
        """`fiesta send-due` beside a live bot must not post it again."""
        queue_one(file_session)
        slack = FakeSlack()

        with drain_lock():  # stands in for the bot's scheduler mid-pass
            sent = sender.drain(file_session, slack, pace=False)

        assert sent == 0
        assert slack.sent == []

    def test_the_message_still_goes_out_once_the_lock_is_free(
        self, file_session
    ):
        queue_one(file_session)
        slack = FakeSlack()

        with drain_lock():
            sender.drain(file_session, slack, pace=False)
        sent = sender.drain(file_session, slack, pace=False)

        assert sent == 1
        assert len(slack.sent) == 1
