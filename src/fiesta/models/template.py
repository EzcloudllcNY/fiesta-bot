"""Congratulation texts."""

from datetime import datetime

from sqlmodel import Field, SQLModel

from fiesta import clock
from fiesta.columns import UtcDateTime


class CongratsTemplate(SQLModel, table=True):
    """A congratulation for one person; ``{user}`` becomes their mention."""

    __tablename__ = "congrats_template"

    id: int | None = Field(default=None, primary_key=True)
    text: str
    enabled: bool = Field(default=True, index=True)
    created_at: datetime = Field(
        default_factory=lambda: clock.now_utc(), sa_type=UtcDateTime
    )
