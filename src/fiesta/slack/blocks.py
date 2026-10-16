"""Every message the bot sends, as Block Kit.

Builders return block *lists*, never strings. That is what lets phase 2 add a
GIF by inserting one image block, with no change to the sender, the outbox or
the services.

Mentions use the ``<@U123>`` form: Slack resolves the display name itself, so
nothing here needs a users.info call and names can never go stale. The form only
renders inside a ``mrkdwn`` field — in ``plain_text`` it shows up literally.
"""

import calendar
from enum import StrEnum
from typing import Any

Block = dict[str, Any]


class AskTone(StrEnum):
    """Why this person is being asked — all the opening line depends on.

    The distinction that matters is whose arrival prompted the message. WELCOME
    goes to someone who just joined a workspace the bot was already in;
    INTRODUCTION is the other way round, and goes out once when the bot itself
    is the new arrival.
    """

    WELCOME = "welcome"
    INTRODUCTION = "introduction"
    REMINDER = "reminder"


OPENINGS = {
    AskTone.WELCOME: "Welcome aboard! :tada: I keep track of team birthdays.",
    AskTone.INTRODUCTION: (
        ":wave: Hi! I'm Fiesta, and I'm new here. "
        "I keep track of team birthdays so "
        "nobody gets forgotten."
    ),
    AskTone.REMINDER: "Still no birthday from you — mind sharing it?",
}

# Interaction ids, shared with the handlers that respond to them.
ACTION_SET_BIRTHDAY = "set_birthday"
ACTION_NEVER_ASK = "never_ask"
ACTION_MUTE_UPCOMING = "mute_upcoming"
ACTION_DELETE_BIRTHDAY = "delete_birthday"
VIEW_BIRTHDAY_MODAL = "birthday_modal"
BLOCK_MONTH = "month_block"
BLOCK_DAY = "day_block"
BLOCK_YEAR = "year_block"
BLOCK_PREFS = "prefs_block"
BLOCK_DELETE = "delete_block"
INPUT_MONTH = "month"
INPUT_DAY = "day"
INPUT_YEAR = "year"
INPUT_PREFS = "prefs"
PREF_HIDE_BIRTHDAY = "hide_birthday"
PREF_MUTE_UPCOMING = "mute_upcoming"


def mention(user_id: str) -> str:
    return f"<@{user_id}>"


def _section(text: str) -> Block:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(text: str) -> Block:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _checkbox_option(*, text: str, value: str, description: str) -> Block:
    return {
        "text": {"type": "plain_text", "text": text},
        "value": value,
        "description": {"type": "plain_text", "text": description},
    }


def _button(text: str, action_id: str, *, style: str | None = None) -> Block:
    button: Block = {
        "type": "button",
        "text": {"type": "plain_text", "text": text, "emoji": True},
        "action_id": action_id,
    }
    if style:
        button["style"] = style
    return button


def ask_blocks(*, tone: AskTone, asks_left: int) -> list[Block]:
    """The DM that asks a member for their birthday.

    The final ask says so, so that someone who simply isn't interested knows
    they can ignore it without the bot nagging them forever.
    """
    blocks = [
        _section(
            f"{OPENINGS[tone]}\n\n"
            "Tell me the day and I'll make sure the team celebrates it."
        ),
        {
            "type": "actions",
            "elements": [
                _button(
                    "Set my birthday", ACTION_SET_BIRTHDAY, style="primary"
                ),
                _button("Never ask me", ACTION_NEVER_ASK),
            ],
        },
        _context("Your birth year stays private — only the day is ever shown."),
    ]

    if asks_left == 0:
        blocks.append(_context("This is the last time I'll ask."))

    return blocks


def birthday_modal(
    *,
    current: tuple[int, int, int | None] | None = None,
    hidden: bool = False,
    muted: bool = False,
) -> dict[str, Any]:
    """The add/change/delete modal behind /birthday and "Set my birthday".

    Every day from 1 to 31 is offered for every month; an impossible combination
    like 31 February is rejected on submit with an inline error, which is
    simpler than repainting the day list each time the month changes.

    The two privacy switches ride along as checkboxes rather than living behind
    their own command: someone who wants to be left out is already in this view,
    and saving them together means one place can answer "what does the bot know
    about me".

    Deleting is a button, not a second submit, because a modal only gets one. It
    is only offered when there is something to delete.
    """
    month_options = [
        {
            "text": {"type": "plain_text", "text": calendar.month_name[m]},
            "value": str(m),
        }
        for m in range(1, 13)
    ]
    day_options = [
        {"text": {"type": "plain_text", "text": str(d)}, "value": str(d)}
        for d in range(1, 32)
    ]

    month_element: Block = {
        "type": "static_select",
        "action_id": INPUT_MONTH,
        "placeholder": {"type": "plain_text", "text": "Month"},
        "options": month_options,
    }
    day_element: Block = {
        "type": "static_select",
        "action_id": INPUT_DAY,
        "placeholder": {"type": "plain_text", "text": "Day"},
        "options": day_options,
    }
    year_element: Block = {
        "type": "plain_text_input",
        "action_id": INPUT_YEAR,
        "placeholder": {"type": "plain_text", "text": "1990"},
    }

    if current is not None:
        month, day, year = current
        month_element["initial_option"] = month_options[month - 1]
        day_element["initial_option"] = day_options[day - 1]
        if year is not None:
            year_element["initial_value"] = str(year)

    pref_options = [
        _checkbox_option(
            text="Hide my birthday",
            value=PREF_HIDE_BIRTHDAY,
            description="No heads-up DMs about me, and no post on the day.",
        ),
        _checkbox_option(
            text="Mute other people's birthdays",
            value=PREF_MUTE_UPCOMING,
            description=(
                "Stop the DMs telling me a teammate's birthday is coming up."
            ),
        ),
    ]
    prefs_element: Block = {
        "type": "checkboxes",
        "action_id": INPUT_PREFS,
        "options": pref_options,
    }
    chosen = [
        option
        for option, is_on in zip(pref_options, (hidden, muted), strict=True)
        if is_on
    ]
    if chosen:
        # Slack rejects the view outright if initial_options is present but
        # empty.
        prefs_element["initial_options"] = chosen

    return {
        "type": "modal",
        "callback_id": VIEW_BIRTHDAY_MODAL,
        "title": {"type": "plain_text", "text": "Your birthday"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": BLOCK_MONTH,
                "label": {"type": "plain_text", "text": "Month"},
                "element": month_element,
            },
            {
                "type": "input",
                "block_id": BLOCK_DAY,
                "label": {"type": "plain_text", "text": "Day"},
                "element": day_element,
            },
            {
                "type": "input",
                "block_id": BLOCK_YEAR,
                "optional": True,
                "label": {"type": "plain_text", "text": "Year (optional)"},
                "hint": {
                    "type": "plain_text",
                    "text": "Never shown to anyone.",
                },
                "element": year_element,
            },
            {
                "type": "input",
                "block_id": BLOCK_PREFS,
                "optional": True,
                "label": {"type": "plain_text", "text": "Privacy"},
                "element": prefs_element,
            },
            *([_delete_block()] if current is not None else []),
        ],
    }


def _delete_block() -> Block:
    return {
        "type": "actions",
        "block_id": BLOCK_DELETE,
        "elements": [
            _button(
                "Remove my birthday", ACTION_DELETE_BIRTHDAY, style="danger"
            )
        ],
    }


def deleted_modal() -> dict[str, Any]:
    """What the modal turns into once the member removes their birthday.

    Swapping the view in place is the only honest acknowledgement available: a
    modal cannot post a message to itself, and leaving the form standing would
    suggest the delete had not happened.
    """
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "Your birthday"},
        "close": {"type": "plain_text", "text": "Done"},
        "blocks": [_section(deleted_confirmation())],
    }


def upcoming_blocks(
    *, birthday_user_id: str, days_away: int, month: int, day: int
) -> list[Block]:
    """The private heads-up every *other* member gets a few days early."""
    when = "tomorrow" if days_away == 1 else f"in {days_away} days"
    date_text = f"{calendar.month_name[month]} {day}"

    return [
        _section(
            f":birthday: {mention(birthday_user_id)}'s birthday is {when} "
            f"— *{date_text}*."
            "\n\nTime to think of something nice to say."
        ),
        {
            "type": "actions",
            "elements": [_button("Mute these reminders", ACTION_MUTE_UPCOMING)],
        },
    ]


def celebration_blocks(*, text: str, gif_url: str | None = None) -> list[Block]:
    """The channel post on the day.

    ``text`` arrives with its placeholder already filled in. The GIF is optional
    and always last, so a missing or broken one costs nothing — the message
    still reads fine.
    """
    blocks: list[Block] = [_section(text)]

    if gif_url:
        blocks.append(
            {
                "type": "image",
                "image_url": gif_url,
                "alt_text": "birthday gif",
            }
        )

    return blocks


def saved_confirmation(*, month: int, day: int, hidden: bool = False) -> str:
    saved = (
        f":white_check_mark: Saved — *{calendar.month_name[month]} {day}*. "
        "Run `/birthday` any time to change or remove it."
    )
    if hidden:
        saved += "\n\nIt's hidden, so I won't announce it to anyone."
    return saved


def deleted_confirmation() -> str:
    return ":wastebasket: Your birthday is deleted. I won't mention it again."


def declined_confirmation() -> str:
    return (
        "Got it — I won't ask again. "
        "Run `/birthday` if you ever change your mind."
    )


def muted_confirmation() -> str:
    return "Muted. You won't get upcoming birthday reminders any more."
