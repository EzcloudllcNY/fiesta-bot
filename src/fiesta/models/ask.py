"""State of "have we asked this person for their birthday yet?"."""

from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel

from fiesta import clock
from fiesta.columns import UtcDateTime, enum_column


class AskStatus(StrEnum):
    PENDING = "pending"  # not asked yet; waiting out the post-join delay
    ASKED = "asked"  # asked at least once, no answer yet
    ANSWERED = "answered"  # birthday saved
    DECLINED = "declined"  # chose "Never ask me"
    EXHAUSTED = "exhausted"  # ran out of reminders; we give up


TERMINAL_STATUSES = frozenset(
    {AskStatus.ANSWERED, AskStatus.DECLINED, AskStatus.EXHAUSTED}
)


class AskState(SQLModel, table=True):
    __tablename__ = "ask_state"

    member_id: str = Field(
        primary_key=True,
        foreign_key="member.slack_user_id",
        ondelete="CASCADE",
    )
    status: AskStatus = Field(
        default=AskStatus.PENDING, index=True, sa_type=enum_column(AskStatus)
    )
    asks_sent: int = 0
    last_asked_at: datetime | None = Field(default=None, sa_type=UtcDateTime)
    next_ask_at: datetime | None = Field(
        default=None, index=True, sa_type=UtcDateTime
    )
    created_at: datetime = Field(
        default_factory=lambda: clock.now_utc(), sa_type=UtcDateTime
    )
    updated_at: datetime = Field(
        default_factory=lambda: clock.now_utc(), sa_type=UtcDateTime
    )
