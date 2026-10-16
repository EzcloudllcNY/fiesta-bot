"""Database engine and session handling."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine

_engine: Engine | None = None


def _apply_pragmas(dbapi_connection, _connection_record) -> None:
    """Settings every connection needs.

    WAL lets the scheduler read while a handler writes. ``foreign_keys`` is off
    by default in SQLite, so the ``ON DELETE CASCADE`` clauses in the schema
    would silently do nothing without it.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def make_engine(database_url: str, *, echo: bool = False, **kwargs) -> Engine:
    engine = create_engine(database_url, echo=echo, **kwargs)
    event.listen(engine, "connect", _apply_pragmas)
    return engine


def init_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create the process-wide engine, making the parent directory if needed."""
    global _engine
    if database_url.startswith("sqlite:///") and not database_url.endswith(
        ":memory:"
    ):
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(
            parents=True, exist_ok=True
        )
    _engine = make_engine(database_url, echo=echo)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Engine not initialised — call init_engine() first")
    return _engine


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """A transaction: commits on success, rolls back on any exception."""
    with Session(engine or get_engine()) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def create_all(engine: Engine) -> None:
    """Create tables straight from the models. Tests only, never production."""
    from fiesta import (
        models,  # noqa: F401  (registers every table on SQLModel.metadata)
    )

    SQLModel.metadata.create_all(engine)
