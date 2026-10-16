"""Workspace events: people joining, and profile changes."""

import logging

from slack_bolt import App

from fiesta.db import session_scope
from fiesta.repo import asks as asks_repo
from fiesta.repo import members as members_repo
from fiesta.repo import settings as settings_repo
from fiesta.services.roster import display_name

log = logging.getLogger(__name__)


def register(app: App) -> None:
    @app.event("team_join")
    def _on_team_join(event) -> None:
        on_team_join(event["user"])

    @app.event("user_change")
    def _on_user_change(event) -> None:
        on_user_change(event["user"])


def on_team_join(user: dict) -> None:
    """Record a new member and queue them to be asked once the delay is up.

    The daily roster sync would find them eventually; this just makes the
    welcome prompt timely while the bot is running.
    """
    user_id = user["id"]
    if user.get("is_bot") or user_id == "USLACKBOT":
        return

    with session_scope() as session:
        members_repo.upsert(
            session,
            slack_user_id=user_id,
            display_name=display_name(user),
            real_name=user.get("real_name"),
            tz=user.get("tz"),
        )
        delay = settings_repo.get(session, "ask_delay_days")
        asks_repo.create_pending(session, user_id, delay_days=delay)

    log.info(
        "New member %s queued for a birthday ask in %d day(s)", user_id, delay
    )


def on_user_change(user: dict) -> None:
    """Keep names and deactivation status fresh between roster syncs."""
    with session_scope() as session:
        members_repo.upsert(
            session,
            slack_user_id=user["id"],
            display_name=display_name(user),
            real_name=user.get("real_name"),
            tz=user.get("tz"),
            is_bot=bool(user.get("is_bot")),
            is_deleted=bool(user.get("deleted")),
        )
