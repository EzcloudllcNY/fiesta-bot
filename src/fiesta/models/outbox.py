"""The outbound message queue.

Scheduled jobs never call Slack directly. They write rows here and the sender
drains them. That indirection is what makes every job safe to run twice. The
one exception is a handler answering a click, which replies straight away.
"""

import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlmodel import Field, SQLModel

from fiesta import clock
from fiesta.columns import UtcDateTime, enum_column


class MessageKind(StrEnum):
    ASK = "ask"  # "what's your birthday?" DM
    REMIND = "remind"  # a follow-up ask
    UPCOMING = "upcoming"  # "X's birthday is in 3 days" DM
    CELEBRATION = "celebration"  # the channel post on the day


class OutboxStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"  # retried and gave up
    SKIPPED = "skipped"  # rejected in a way retrying can't fix


class OutboxMessage(SQLModel, table=True):
    __tablename__ = "outbox"

    id: int | None = Field(default=None, primary_key=True)
    kind: MessageKind = Field(sa_type=enum_column(MessageKind))
    channel: str  # a channel id, or a user id to open a DM
    blocks_json: str
    fallback_text: str  # what shows in the notification / on old clients
    dedupe_key: str = Field(unique=True, index=True)
    status: OutboxStatus = Field(
        default=OutboxStatus.PENDING,
        index=True,
        sa_type=enum_column(OutboxStatus),
    )
    attempts: int = 0
    scheduled_for: datetime = Field(
        default_factory=lambda: clock.now_utc(), index=True, sa_type=UtcDateTime
    )
    sent_at: datetime | None = Field(default=None, sa_type=UtcDateTime)
    slack_ts: str | None = None
    last_error: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: clock.now_utc(), sa_type=UtcDateTime
    )

    @property
    def blocks(self) -> list[dict[str, Any]]:
        return json.loads(self.blocks_json)
