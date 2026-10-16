"""Alembic environment.

The database URL comes from the app's own config rather than alembic.ini, so
there is exactly one place that decides where the database lives.
"""

from logging.config import fileConfig

from alembic import context
from sqlmodel import SQLModel

from fiesta import (
    models,  # noqa: F401  — registers every table on SQLModel.metadata
)
from fiesta.config import load_config
from fiesta.db import make_engine

config = context.config

if config.config_file_name is not None:
    # Keep the app's loggers alive when migrations run in-process (tests do).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = SQLModel.metadata


def _database_url() -> str:
    """Prefer a configured URL (the CLI sets one), else read the env."""
    return (
        config.get_main_option("sqlalchemy.url")
        or load_config(require_slack=False).database_url
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(_database_url())

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most things in place; batch mode rewrites the
            # table instead, which is what makes future migrations possible at
            # all.
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
