"""Custom column types."""

from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import DateTime
from sqlalchemy import Enum as Enum_
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator):
    """A datetime column that is always timezone-aware in Python.

    SQLite has no datetime type: SQLAlchemy stores an ISO-ish string and
    silently drops the offset on the way in, so a value written as aware UTC
    reads back naive. Mixing the two then fails at the point of arithmetic — far
    from the code that caused it.

    This normalises both directions: everything is stored as naive UTC, which
    keeps the stored strings sortable and comparable in SQL, and everything
    comes back tagged UTC, so Python never sees a naive datetime at all.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                f"Refusing to store a naive datetime: {value!r}. "
                "Use clock.now_utc()."
            )
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, dialect
    ) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


def enum_column(enum_class: type[Enum]) -> Enum_:
    """A string column storing an enum's *values* rather than its member names.

    SQLAlchemy stores `AskStatus.PENDING` as "PENDING" by default. These are
    StrEnums whose values are already the lowercase strings the schema
    documents, and the database is meant to be readable directly — it is
    bind-mounted so that `sqlite3 fiesta.db` works — so storing "pending" keeps
    hand-written SQL honest.
    """
    return Enum_(
        enum_class,
        values_callable=lambda members: [member.value for member in members],
        native_enum=False,  # SQLite has no enum type, so this is a VARCHAR
        # Worth the constraint precisely because the database is meant to be
        # edited by hand: a typo in an UPDATE is otherwise a silently
        # unreachable row.
        create_constraint=True,
    )
