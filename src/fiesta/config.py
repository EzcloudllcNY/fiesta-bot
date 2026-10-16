"""Environment configuration: secrets and infrastructure only.

Everything an admin might want to change at runtime (posting time, channel,
reminder counts) lives in the ``setting`` table instead, so it can be changed
without a redeploy.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Config:
    slack_bot_token: str
    slack_app_token: str
    db_path: Path
    log_level: str
    sentry_dsn: str
    sentry_environment: str
    sentry_release: str

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"


def load_config(*, require_slack: bool = True) -> Config:
    """Read configuration from the environment and any ``.env`` file.

    ``require_slack`` is off for commands that only touch the database (``fiesta
    migrate``, ``fiesta settings``), so they work before any Slack credentials
    exist.
    """
    load_dotenv()

    bot_token = os.getenv("SLACK_BOT_TOKEN", "")
    app_token = os.getenv("SLACK_APP_TOKEN", "")
    if require_slack and not (bot_token and app_token):
        missing = [
            name
            for name, value in (
                ("SLACK_BOT_TOKEN", bot_token),
                ("SLACK_APP_TOKEN", app_token),
            )
            if not value
        ]
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    return Config(
        slack_bot_token=bot_token,
        slack_app_token=app_token,
        db_path=Path(os.getenv("FIESTA_DB_PATH", "./data/fiesta.db")),
        log_level=os.getenv("FIESTA_LOG_LEVEL", "INFO"),
        sentry_dsn=os.getenv("SENTRY_DSN", ""),
        sentry_environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
        sentry_release=os.getenv("SENTRY_RELEASE", ""),
    )
