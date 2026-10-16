"""Button presses."""

from slack_bolt import App

from fiesta.db import session_scope
from fiesta.repo import asks as asks_repo
from fiesta.repo import birthdays as birthdays_repo
from fiesta.repo import members as members_repo
from fiesta.services.roster import display_name
from fiesta.slack import blocks
from fiesta.slack.handlers.views import birthday_modal_for


def register(app: App) -> None:
    @app.action(blocks.ACTION_SET_BIRTHDAY)
    def _on_set(ack, body, client) -> None:
        ack()
        open_birthday_modal(body=body, client=client)

    @app.action(blocks.ACTION_NEVER_ASK)
    def _on_never_ask(ack, body, client) -> None:
        ack()
        never_ask(body=body, client=client)

    @app.action(blocks.ACTION_MUTE_UPCOMING)
    def _on_mute(ack, body, client) -> None:
        ack()
        mute_upcoming(body=body, client=client)

    @app.action(blocks.ACTION_DELETE_BIRTHDAY)
    def _on_delete(ack, body, client) -> None:
        ack()
        delete_birthday(body=body, client=client)


def open_birthday_modal(*, body, client) -> None:
    """Open the modal, pre-filled if the member already told us."""
    client.views_open(
        trigger_id=body["trigger_id"],
        view=birthday_modal_for(body["user"]["id"]),
    )


def delete_birthday(*, body, client) -> None:
    """Remove the birthday outright and say so in place of the form.

    The row is deleted rather than flagged: the requirement is that a deleted
    birthday is gone, and the member can always enter it again. Their ask state
    stays terminal, so removing it is not read by the bot as an invitation to
    start asking again.
    """
    with session_scope() as session:
        birthdays_repo.remove(session, body["user"]["id"])

    client.views_update(view_id=body["view"]["id"], view=blocks.deleted_modal())


def never_ask(*, body, client) -> None:
    with session_scope() as session:
        user_id = body["user"]["id"]
        members_repo.ensure(
            session, user_id, display_name=display_name(body["user"])
        )
        asks_repo.mark_declined(session, user_id)

    _replace_prompt(body, client, blocks.declined_confirmation())


def mute_upcoming(*, body, client) -> None:
    with session_scope() as session:
        user_id = body["user"]["id"]
        members_repo.ensure(
            session, user_id, display_name=display_name(body["user"])
        )
        members_repo.set_muted_upcoming(session, user_id, True)

    _replace_prompt(body, client, blocks.muted_confirmation())


def _replace_prompt(body, client, text: str) -> None:
    """Swap the prompt for a confirmation.

    Replacing rather than replying stops the buttons being pressed twice and
    keeps the DM from growing a trail of dead messages.
    """
    client.chat_update(
        channel=body["channel"]["id"],
        ts=body["message"]["ts"],
        blocks=[],
        text=text,
    )
