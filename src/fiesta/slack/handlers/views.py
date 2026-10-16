"""Modal submissions.

The work lives in plain functions and the Bolt decorator only forwards to them,
so the behaviour can be tested directly instead of through a live Bolt app.
"""

from slack_bolt import App

from fiesta.db import session_scope
from fiesta.repo import asks as asks_repo
from fiesta.repo import birthdays as birthdays_repo
from fiesta.repo import members as members_repo
from fiesta.services.roster import display_name
from fiesta.slack import blocks


def birthday_modal_for(user_id: str) -> dict:
    """The modal as this member should see it.

    Their birthday and their switches.

    Both the slash command and the "Set my birthday" button open the same view,
    so the state-gathering lives here rather than being written out twice.
    """
    with session_scope() as session:
        existing = birthdays_repo.get(session, user_id)
        current = (
            (existing.month, existing.day, existing.year) if existing else None
        )
        hidden = bool(existing and existing.is_hidden)
        muted = user_id in members_repo.muted_ids(session)

    return blocks.birthday_modal(current=current, hidden=hidden, muted=muted)


def register(app: App) -> None:
    @app.view(blocks.VIEW_BIRTHDAY_MODAL)
    def _on_submit(ack, body, view, client) -> None:
        save_birthday(ack=ack, body=body, view=view, client=client)


def save_birthday(*, ack, body, view, client) -> None:
    """Validate and store what the member typed."""
    user_id = body["user"]["id"]
    values = view["state"]["values"]

    month = int(
        values[blocks.BLOCK_MONTH][blocks.INPUT_MONTH]["selected_option"][
            "value"
        ]
    )
    day = int(
        values[blocks.BLOCK_DAY][blocks.INPUT_DAY]["selected_option"]["value"]
    )

    # Every month offers all 31 days, so combinations like 31 February arrive
    # here and are answered with an inline error against the field that caused
    # them.
    try:
        year = parse_year(
            values[blocks.BLOCK_YEAR][blocks.INPUT_YEAR].get("value")
        )
        birthdays_repo.validate(month, day, year)
    except ValueError as exc:
        field = blocks.BLOCK_YEAR if "Year" in str(exc) else blocks.BLOCK_DAY
        ack(response_action="errors", errors={field: str(exc)})
        return

    ack()
    prefs = selected_prefs(values)
    hidden = blocks.PREF_HIDE_BIRTHDAY in prefs

    with session_scope() as session:
        members_repo.ensure(
            session, user_id, display_name=display_name(body["user"])
        )
        birthdays_repo.upsert(
            session,
            member_id=user_id,
            month=month,
            day=day,
            year=year,
        )
        birthdays_repo.set_hidden(session, user_id, hidden)
        members_repo.set_muted_upcoming(
            session, user_id, blocks.PREF_MUTE_UPCOMING in prefs
        )
        asks_repo.mark_answered(session, user_id)

    client.chat_postMessage(
        channel=user_id,
        text=blocks.saved_confirmation(month=month, day=day, hidden=hidden),
    )


def selected_prefs(values: dict) -> set[str]:
    """The privacy checkboxes that were ticked.

    An unticked checkbox group is absent from the payload rather than present
    and empty, so every lookup here has to tolerate a missing key.
    """
    block = values.get(blocks.BLOCK_PREFS, {}).get(blocks.INPUT_PREFS, {})
    return {option["value"] for option in block.get("selected_options") or []}


def parse_year(raw: str | None) -> int | None:
    """The year is optional free text, so blanks mean "not given"."""
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        raise ValueError(f"Year must be a number, got {raw!r}") from None
