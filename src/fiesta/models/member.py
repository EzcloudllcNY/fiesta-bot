"""The workspace roster, mirrored from Slack, plus the preferences we own."""

from datetime import datetime

from sqlmodel import Field, SQLModel

from fiesta import clock
from fiesta.columns import UtcDateTime


class Member(SQLModel, table=True):
    """A Slack user. Overwritten wholesale by the roster sync, so nothing the
    *user* chooses may live here — those fields belong on MemberPrefs."""

    __tablename__ = "member"

    slack_user_id: str = Field(primary_key=True)
    display_name: str
    real_name: str | None = None
    tz: str | None = None
    is_bot: bool = Field(default=False, index=True)
    is_deleted: bool = Field(default=False, index=True)
    joined_at: datetime | None = Field(default=None, sa_type=UtcDateTime)
    created_at: datetime = Field(
        default_factory=lambda: clock.now_utc(), sa_type=UtcDateTime
    )
    updated_at: datetime = Field(
        default_factory=lambda: clock.now_utc(), sa_type=UtcDateTime
    )

    @property
    def is_active(self) -> bool:
        """A real person who can still receive messages."""
        return not self.is_bot and not self.is_deleted


class MemberPrefs(SQLModel, table=True):
    """Choices a member made.

    Kept out of Member so the roster sync can't clobber them.
    """

    __tablename__ = "member_prefs"

    member_id: str = Field(
        primary_key=True,
        foreign_key="member.slack_user_id",
        ondelete="CASCADE",
    )
    muted_upcoming: bool = False
    created_at: datetime = Field(
        default_factory=lambda: clock.now_utc(), sa_type=UtcDateTime
    )
    updated_at: datetime = Field(
        default_factory=lambda: clock.now_utc(), sa_type=UtcDateTime
    )
