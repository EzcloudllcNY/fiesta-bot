"""Stored birthdays."""

from datetime import datetime

from sqlmodel import Field, SQLModel

from fiesta import clock
from fiesta.columns import UtcDateTime


class Birthday(SQLModel, table=True):
    """One member's birthday.

    ``year`` is optional and is never rendered anywhere — it is accepted only so
    a member who wants to give it isn't forced to lie. Nothing reads it back
    out.
    """

    __tablename__ = "birthday"

    member_id: str = Field(
        primary_key=True,
        foreign_key="member.slack_user_id",
        ondelete="CASCADE",
    )
    month: int = Field(index=True)
    day: int = Field(index=True)
    year: int | None = None
    is_hidden: bool = False
    created_at: datetime = Field(
        default_factory=lambda: clock.now_utc(), sa_type=UtcDateTime
    )
    updated_at: datetime = Field(
        default_factory=lambda: clock.now_utc(), sa_type=UtcDateTime
    )
