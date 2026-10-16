"""Curated celebration GIFs — phase 2.

The table and its queries exist now so the schema is settled; ``services.gifs``
returns None until the list is populated. URLs rather than blobs: a few hundred
GIFs would be a ~200 MB database and would need a Slack upload path, for no
gain.
"""

from datetime import datetime

from sqlmodel import Field, SQLModel

from fiesta import clock
from fiesta.columns import UtcDateTime

MAX_FAILURES = 3
"""After this many failed renders a GIF is left out of the rotation."""


class Gif(SQLModel, table=True):
    __tablename__ = "gif"

    id: int | None = Field(default=None, primary_key=True)
    url: str = Field(unique=True)
    tags: str | None = None
    enabled: bool = Field(default=True, index=True)
    fail_count: int = 0
    last_used_at: datetime | None = Field(
        default=None, index=True, sa_type=UtcDateTime
    )
    created_at: datetime = Field(
        default_factory=lambda: clock.now_utc(), sa_type=UtcDateTime
    )
