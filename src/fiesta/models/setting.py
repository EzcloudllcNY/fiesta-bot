"""Admin settings, stored as JSON so they can change without a redeploy."""

from datetime import datetime

from sqlmodel import Field, SQLModel

from fiesta import clock
from fiesta.columns import UtcDateTime


class Setting(SQLModel, table=True):
    __tablename__ = "setting"

    key: str = Field(primary_key=True)
    value: str  # JSON-encoded
    updated_at: datetime = Field(
        default_factory=lambda: clock.now_utc(), sa_type=UtcDateTime
    )
