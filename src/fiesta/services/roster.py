"""Keeping the local roster in step with Slack.

This is the safety net for events missed while the bot was down: team_join keeps
things current in real time, and this sweep repairs whatever slipped through.
"""

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from sqlmodel import Session

from fiesta.repo import asks as asks_repo
from fiesta.repo import members as members_repo
from fiesta.repo import settings as settings_repo

log = logging.getLogger(__name__)


class SlackUserLister(Protocol):
    """The part of the Slack client this module needs, so tests can stub it."""

    def users_list(
        self, *, cursor: str | None = ..., limit: int = ...
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class SyncResult:
    seen: int
    deactivated: int
    newly_queued: int


def sync(session: Session, client: SlackUserLister) -> SyncResult:
    """Pull the full user list and reconcile it with what we have stored."""
    ask_delay = settings_repo.get(session, "ask_delay_days")

    seen_ids: set[str] = set()
    newly_queued = 0

    for user in _iter_users(client):
        user_id = user["id"]
        seen_ids.add(user_id)

        is_bot = bool(user.get("is_bot")) or user_id == "USLACKBOT"
        is_deleted = bool(user.get("deleted"))

        members_repo.upsert(
            session,
            slack_user_id=user_id,
            display_name=display_name(user),
            real_name=user.get("real_name"),
            tz=user.get("tz"),
            is_bot=is_bot,
            is_deleted=is_deleted,
        )

        # Only real, current people get asked; create_pending leaves alone
        # anyone who already answered or opted out.
        if (
            not is_bot
            and not is_deleted
            and asks_repo.get(session, user_id) is None
        ):
            asks_repo.create_pending(session, user_id, delay_days=ask_delay)
            newly_queued += 1

    # Anyone we know about who no longer appears has left the workspace.
    vanished = members_repo.all_ids(session) - seen_ids
    deactivated = members_repo.mark_deleted(session, vanished)

    log.info(
        "Roster sync: %d users seen, %d deactivated, "
        "%d newly queued for an ask",
        len(seen_ids),
        deactivated,
        newly_queued,
    )
    return SyncResult(
        seen=len(seen_ids), deactivated=deactivated, newly_queued=newly_queued
    )


def _iter_users(client: SlackUserLister):
    """Walk every page of users.list."""
    cursor: str | None = None
    while True:
        response = client.users_list(cursor=cursor, limit=200)
        yield from response["members"]

        cursor = (
            response.get("response_metadata", {}).get("next_cursor") or None
        )
        if not cursor:
            return


def display_name(user: dict[str, Any]) -> str:
    """The best name Slack offers, falling back through its usual ladder."""
    profile = user.get("profile") or {}
    for candidate in (
        profile.get("display_name"),
        profile.get("real_name"),
        user.get("real_name"),
        user.get("name"),
    ):
        if candidate:
            return candidate
    return user["id"]
