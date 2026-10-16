"""The migrations and the models have to describe the same schema.

The test suite builds its database with `create_all`, straight from the models;
production only ever gets there through Alembic. Nothing else in the suite would
notice a model that gained a column whose migration was never written — every
test would pass, and the first write in production would fail.
"""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlmodel import SQLModel

from fiesta import (
    models,  # noqa: F401  (registers every table on SQLModel.metadata)
)
from fiesta.db import make_engine
from fiesta.migrations import upgrade_to_head

# Alembic's own bookkeeping table is in the database by design and not in the
# models.
ALEMBIC_TABLES = {"alembic_version"}


def named(difference) -> str:
    """The table a single autogenerate difference is about."""
    if difference[0] in {"add_table", "remove_table"}:
        return difference[1].name
    return (
        difference[0][1]
        if isinstance(difference[0], tuple)
        else str(difference[2])
    )


@pytest.fixture
def migrated(tmp_path):
    """A database built the way production builds it: `alembic upgrade head`."""
    url = f"sqlite:///{tmp_path / 'fiesta.db'}"
    upgrade_to_head(url)
    engine = make_engine(url)

    yield engine

    engine.dispose()


def test_the_migrations_build_exactly_what_the_models_describe(migrated):
    with migrated.connect() as connection:
        context = MigrationContext.configure(connection)
        differences = compare_metadata(context, SQLModel.metadata)

    unexplained = [d for d in differences if named(d) not in ALEMBIC_TABLES]
    assert unexplained == [], (
        "The models and the migrations have drifted. Run "
        "`uv run alembic revision --autogenerate -m '...'` "
        "and commit the result."
    )


def test_every_model_table_exists_after_migrating(migrated):
    """A blunter check that survives whatever autogenerate decides to ignore."""
    from sqlalchemy import inspect

    present = set(inspect(migrated).get_table_names())

    assert set(SQLModel.metadata.tables) <= present
