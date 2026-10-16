"""Running Alembic from inside the application.

`fiesta migrate` is the only way the schema is ever created in production —
`create_all` is a test convenience — so the configuration lives here rather than
inline in the CLI, where a test could only ever check a copy of it.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config


def project_root() -> Path:
    """The directory holding alembic.ini.

    The repo root, or /app in the image.
    """
    return Path(__file__).resolve().parents[2]


def alembic_config(database_url: str) -> Config:
    config = Config(str(project_root() / "alembic.ini"))
    config.set_main_option(
        "script_location", str(project_root() / "migrations")
    )
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def upgrade_to_head(database_url: str) -> None:
    command.upgrade(alembic_config(database_url), "head")
