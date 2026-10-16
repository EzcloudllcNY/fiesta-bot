"""Shared test fixtures.

Three things every test needs: a database, a clock that doesn't move, and a
Slack that isn't real. Each is a fixture here so the tests themselves stay about
behaviour.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session

import fiesta.config
import fiesta.db
from fiesta import clock
from fiesta.db import create_all, make_engine

# A fixed "now" for every test. A Wednesday in a non-leap year, chosen so that
# leap-day behaviour has to be asked for explicitly rather than showing up by
# accident.
DEFAULT_NOW = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    """Keep a developer's real ``.env`` out of the tests.

    Otherwise a filled-in token leaks into every test that reads config, and a
    test about missing credentials passes or fails depending on whose machine
    it runs on.
    """
    monkeypatch.setattr(fiesta.config, "load_dotenv", lambda: False)
    # Same for a DSN exported in the shell: tests must never report to Sentry.
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("SENTRY_RELEASE", raising=False)


@pytest.fixture
def session(monkeypatch):
    """A fresh in-memory database with the full schema.

    StaticPool keeps every connection pointed at the same in-memory database —
    without it each new connection would get its own empty one. That is also
    what lets this engine stand in as the process-wide engine, so code which
    opens its own session through ``session_scope()`` sees the same data the
    test set up.
    """
    engine = make_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    create_all(engine)
    monkeypatch.setattr(fiesta.db, "_engine", engine)

    with Session(engine) as session:
        yield session

    engine.dispose()  # otherwise every test leaks a connection


@dataclass
class FrozenClock:
    """A clock the test controls.

    Time only moves when a test says so, which is what lets assertions about
    scheduling be exact instead of approximate.
    """

    now: datetime = DEFAULT_NOW

    def set(self, moment: datetime) -> None:
        self.now = moment

    def advance(
        self, *, days: int = 0, hours: int = 0, minutes: int = 0
    ) -> None:
        self.now += timedelta(days=days, hours=hours, minutes=minutes)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch) -> FrozenClock:
    """Freeze time for every test.

    Autouse because a test that accidentally uses the real clock passes locally
    and fails at midnight, which is the worst kind of flake to chase.
    """
    controller = FrozenClock()
    monkeypatch.setattr(clock, "now_utc", lambda: controller.now)
    return controller


@dataclass
class SentMessage:
    channel: str
    text: str
    blocks: list[dict[str, Any]]

    def mentions(self, user_id: str) -> bool:
        return f"<@{user_id}>" in str(self.blocks)


@dataclass
class FakeSlack:
    """Stands in for the Slack client.

    Records what was sent, and can be told to fail so error handling is testable
    without constructing real API responses at the call site.
    """

    sent: list[SentMessage] = field(default_factory=list)
    users: list[dict[str, Any]] = field(default_factory=list)
    fail_with: Exception | None = None
    fail_times: int = 0

    def chat_postMessage(self, *, channel: str, text: str, blocks=None, **_):
        if self.fail_with is not None and self.fail_times != 0:
            self.fail_times -= 1
            raise self.fail_with

        self.sent.append(
            SentMessage(channel=channel, text=text, blocks=blocks or [])
        )
        return {"ok": True, "ts": f"171000000.{len(self.sent):06d}"}

    def users_list(self, *, cursor: str | None = None, limit: int = 200):
        return {"members": self.users, "response_metadata": {"next_cursor": ""}}

    @property
    def channels_messaged(self) -> list[str]:
        return [message.channel for message in self.sent]


@pytest.fixture
def slack() -> FakeSlack:
    return FakeSlack()
