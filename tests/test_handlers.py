"""What happens when a member actually clicks something.

The handlers keep their work in plain functions and let the Bolt decorator
forward to them, so these tests exercise the real behaviour without standing up
a Bolt app.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from factories import add_birthday, add_member, configure
from fiesta.models import AskStatus
from fiesta.repo import asks as asks_repo
from fiesta.repo import birthdays as birthdays_repo
from fiesta.repo import members as members_repo
from fiesta.slack import blocks
from fiesta.slack.handlers import actions, commands, events, views


@dataclass
class Ack:
    """Records how the handler acknowledged Slack.

    Bolt's ack is how a modal reports validation failures, so what it was called
    with *is* the behaviour under test.
    """

    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, **kwargs) -> None:
        self.calls.append(kwargs)

    @property
    def errors(self) -> dict[str, str]:
        return self.calls[0].get("errors", {}) if self.calls else {}

    @property
    def accepted(self) -> bool:
        return bool(self.calls) and "errors" not in self.calls[0]


@dataclass
class FakeClient:
    """Stands in for the Slack web client the handlers are handed."""

    opened_views: list[dict[str, Any]] = field(default_factory=list)
    updated_views: list[dict[str, Any]] = field(default_factory=list)
    updates: list[dict[str, Any]] = field(default_factory=list)
    posts: list[dict[str, Any]] = field(default_factory=list)

    def views_open(self, *, trigger_id: str, view: dict) -> None:
        self.opened_views.append(view)

    def views_update(self, *, view_id: str, view: dict) -> None:
        self.updated_views.append(view)

    def chat_update(self, **kwargs) -> None:
        self.updates.append(kwargs)

    def chat_postMessage(self, **kwargs) -> None:
        self.posts.append(kwargs)


def modal_submission(
    *,
    user_id: str,
    month: str,
    day: str,
    year: str = "",
    prefs: list[str] | None = None,
):
    """The body and view Slack sends when the birthday modal is submitted.

    Slack leaves an untouched checkbox group out of the payload entirely, so
    ``prefs=None`` is what an ordinary submission really looks like.
    """
    body = {"user": {"id": user_id, "name": "ana"}}
    values = {
        blocks.BLOCK_MONTH: {
            blocks.INPUT_MONTH: {"selected_option": {"value": month}}
        },
        blocks.BLOCK_DAY: {
            blocks.INPUT_DAY: {"selected_option": {"value": day}}
        },
        blocks.BLOCK_YEAR: {blocks.INPUT_YEAR: {"value": year}},
    }
    if prefs is not None:
        values[blocks.BLOCK_PREFS] = {
            blocks.INPUT_PREFS: {
                "selected_options": [{"value": value} for value in prefs]
            }
        }
    return body, {"state": {"values": values}}


def modal_button_press(*, user_id: str, view_id: str = "V123"):
    """A button pressed inside an open modal, not in a message."""
    return {"user": {"id": user_id, "name": "ana"}, "view": {"id": view_id}}


def button_press(*, user_id: str, channel: str = "D_DM", ts: str = "1700.1"):
    return {
        "user": {"id": user_id, "name": "ana"},
        "channel": {"id": channel},
        "message": {"ts": ts},
        "trigger_id": "T123",
    }


class TestSavingABirthday:
    def test_a_valid_birthday_is_stored(self, session):
        add_member(session, "U_ANA")
        ack, client = Ack(), FakeClient()
        body, view = modal_submission(user_id="U_ANA", month="3", day="15")

        views.save_birthday(ack=ack, body=body, view=view, client=client)

        stored = birthdays_repo.get(session, "U_ANA")
        assert (stored.month, stored.day) == (3, 15)
        assert ack.accepted

    def test_saving_stops_the_bot_asking_again(self, session):
        add_member(session, "U_ANA")
        body, view = modal_submission(user_id="U_ANA", month="3", day="15")

        views.save_birthday(
            ack=Ack(), body=body, view=view, client=FakeClient()
        )

        assert asks_repo.get(session, "U_ANA").status == AskStatus.ANSWERED

    def test_the_member_is_told_it_was_saved(self, session):
        add_member(session, "U_ANA")
        client = FakeClient()
        body, view = modal_submission(user_id="U_ANA", month="3", day="15")

        views.save_birthday(ack=Ack(), body=body, view=view, client=client)

        (post,) = client.posts
        assert post["channel"] == "U_ANA"
        assert "March 15" in post["text"]

    def test_an_optional_year_is_stored_when_given(self, session):
        add_member(session, "U_ANA")
        body, view = modal_submission(
            user_id="U_ANA", month="3", day="15", year="1990"
        )

        views.save_birthday(
            ack=Ack(), body=body, view=view, client=FakeClient()
        )

        assert birthdays_repo.get(session, "U_ANA").year == 1990

    def test_a_blank_year_is_accepted(self, session):
        add_member(session, "U_ANA")
        body, view = modal_submission(
            user_id="U_ANA", month="3", day="15", year="   "
        )

        views.save_birthday(
            ack=Ack(), body=body, view=view, client=FakeClient()
        )

        assert birthdays_repo.get(session, "U_ANA").year is None

    def test_changing_a_birthday_replaces_the_old_one(self, session):
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (1, 1))
        body, view = modal_submission(user_id="U_ANA", month="3", day="15")

        views.save_birthday(
            ack=Ack(), body=body, view=view, client=FakeClient()
        )

        stored = birthdays_repo.get(session, "U_ANA")
        assert (stored.month, stored.day) == (3, 15)


class TestRejectingBadInput:
    """The modal offers 31 days for every month, so bad dates get here."""

    def test_an_impossible_date_is_refused_with_an_inline_error(self, session):
        add_member(session, "U_ANA")
        ack, client = Ack(), FakeClient()
        body, view = modal_submission(user_id="U_ANA", month="2", day="31")

        views.save_birthday(ack=ack, body=body, view=view, client=client)

        assert blocks.BLOCK_DAY in ack.errors
        assert birthdays_repo.get(session, "U_ANA") is None

    def test_a_non_numeric_year_is_refused_against_the_year_field(
        self, session
    ):
        add_member(session, "U_ANA")
        ack = Ack()
        body, view = modal_submission(
            user_id="U_ANA", month="3", day="15", year="ninety"
        )

        views.save_birthday(ack=ack, body=body, view=view, client=FakeClient())

        assert blocks.BLOCK_YEAR in ack.errors

    def test_nothing_is_sent_when_the_input_is_refused(self, session):
        add_member(session, "U_ANA")
        client = FakeClient()
        body, view = modal_submission(user_id="U_ANA", month="2", day="31")

        views.save_birthday(ack=Ack(), body=body, view=view, client=client)

        assert client.posts == []

    def test_a_refused_submission_leaves_an_existing_birthday_alone(
        self, session
    ):
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (1, 1))
        body, view = modal_submission(user_id="U_ANA", month="2", day="31")

        views.save_birthday(
            ack=Ack(), body=body, view=view, client=FakeClient()
        )

        stored = birthdays_repo.get(session, "U_ANA")
        assert (stored.month, stored.day) == (1, 1)


class TestOpeningTheModal:
    def test_the_slash_command_opens_an_empty_modal_for_a_new_member(
        self, session
    ):
        add_member(session, "U_ANA")
        client = FakeClient()

        commands.open_modal(
            body={"user_id": "U_ANA", "trigger_id": "T123"}, client=client
        )

        (view,) = client.opened_views
        assert "initial_option" not in view["blocks"][0]["element"]

    def test_the_slash_command_prefills_an_existing_birthday(self, session):
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (3, 15), year=1990)
        client = FakeClient()

        commands.open_modal(
            body={"user_id": "U_ANA", "trigger_id": "T123"}, client=client
        )

        (view,) = client.opened_views
        assert view["blocks"][0]["element"]["initial_option"]["value"] == "3"

    def test_the_button_opens_the_same_modal(self, session):
        add_member(session, "U_ANA")
        client = FakeClient()

        actions.open_birthday_modal(
            body=button_press(user_id="U_ANA"), client=client
        )

        assert (
            client.opened_views[0]["callback_id"] == blocks.VIEW_BIRTHDAY_MODAL
        )


class TestOptingOut:
    def test_never_ask_me_is_remembered(self, session):
        add_member(session, "U_PRIVATE")

        actions.never_ask(
            body=button_press(user_id="U_PRIVATE"), client=FakeClient()
        )

        assert asks_repo.get(session, "U_PRIVATE").status == AskStatus.DECLINED

    def test_the_prompt_is_replaced_so_it_cannot_be_pressed_twice(
        self, session
    ):
        add_member(session, "U_PRIVATE")
        client = FakeClient()

        actions.never_ask(body=button_press(user_id="U_PRIVATE"), client=client)

        (update,) = client.updates
        assert update["blocks"] == []
        assert "won't ask again" in update["text"]

    def test_muting_upcoming_reminders_is_remembered(self, session):
        add_member(session, "U_QUIET")

        actions.mute_upcoming(
            body=button_press(user_id="U_QUIET"), client=FakeClient()
        )

        assert members_repo.muted_ids(session) == {"U_QUIET"}


class TestDeletingABirthday:
    """FR 1 and FR 5.

    A member can remove their birthday, and it goes completely.
    """

    def test_the_row_is_gone_not_just_flagged(self, session):
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (3, 15))

        actions.delete_birthday(
            body=modal_button_press(user_id="U_ANA"), client=FakeClient()
        )

        assert birthdays_repo.get(session, "U_ANA") is None

    def test_the_form_is_replaced_with_a_confirmation(self, session):
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (3, 15))
        client = FakeClient()

        actions.delete_birthday(
            body=modal_button_press(user_id="U_ANA"), client=client
        )

        (view,) = client.updated_views
        assert "deleted" in str(view["blocks"])

    def test_deleting_does_not_restart_the_asking(self, session):
        """Removing a birthday is opting out, not asking to be nagged."""
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (3, 15))
        asks_repo.mark_answered(session, "U_ANA")
        session.commit()

        actions.delete_birthday(
            body=modal_button_press(user_id="U_ANA"), client=FakeClient()
        )

        assert asks_repo.get(session, "U_ANA").status == AskStatus.ANSWERED

    def test_a_delete_button_is_only_offered_when_there_is_one_to_delete(
        self, session
    ):
        add_member(session, "U_ANA")
        client = FakeClient()

        commands.open_modal(
            body={"user_id": "U_ANA", "trigger_id": "T123"}, client=client
        )

        (view,) = client.opened_views
        assert blocks.ACTION_DELETE_BIRTHDAY not in str(view["blocks"])

    def test_the_delete_button_appears_once_a_birthday_exists(self, session):
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (3, 15))
        client = FakeClient()

        commands.open_modal(
            body={"user_id": "U_ANA", "trigger_id": "T123"}, client=client
        )

        (view,) = client.opened_views
        assert blocks.ACTION_DELETE_BIRTHDAY in str(view["blocks"])


class TestPrivacySwitches:
    """FR 5.

    A member can hide their birthday; FR 3: muting has to be reversible.
    """

    def test_hiding_keeps_the_birthday_out_of_everything(self, session):
        add_member(session, "U_SHY")
        body, view = modal_submission(
            user_id="U_SHY",
            month="3",
            day="15",
            prefs=[blocks.PREF_HIDE_BIRTHDAY],
        )

        views.save_birthday(
            ack=Ack(), body=body, view=view, client=FakeClient()
        )

        assert birthdays_repo.get(session, "U_SHY").is_hidden
        assert birthdays_repo.falling_on(session, date(2026, 3, 15)) == []

    def test_a_submission_with_nothing_ticked_leaves_the_birthday_visible(
        self, session
    ):
        add_member(session, "U_ANA")
        body, view = modal_submission(
            user_id="U_ANA", month="3", day="15", prefs=[]
        )

        views.save_birthday(
            ack=Ack(), body=body, view=view, client=FakeClient()
        )

        assert not birthdays_repo.get(session, "U_ANA").is_hidden

    def test_unticking_hide_makes_the_birthday_visible_again(self, session):
        add_member(session, "U_SHY")
        add_birthday(session, "U_SHY", (3, 15), hidden=True)
        body, view = modal_submission(
            user_id="U_SHY", month="3", day="15", prefs=[]
        )

        views.save_birthday(
            ack=Ack(), body=body, view=view, client=FakeClient()
        )

        assert not birthdays_repo.get(session, "U_SHY").is_hidden

    def test_the_confirmation_says_the_birthday_is_hidden(self, session):
        add_member(session, "U_SHY")
        client = FakeClient()
        body, view = modal_submission(
            user_id="U_SHY",
            month="3",
            day="15",
            prefs=[blocks.PREF_HIDE_BIRTHDAY],
        )

        views.save_birthday(ack=Ack(), body=body, view=view, client=client)

        assert "hidden" in client.posts[0]["text"]

    def test_the_modal_shows_the_switches_as_they_stand(self, session):
        add_member(session, "U_SHY", muted=True)
        add_birthday(session, "U_SHY", (3, 15), hidden=True)
        client = FakeClient()

        commands.open_modal(
            body={"user_id": "U_SHY", "trigger_id": "T123"}, client=client
        )

        (view,) = client.opened_views
        prefs = view["blocks"][3]["element"]
        assert {option["value"] for option in prefs["initial_options"]} == {
            blocks.PREF_HIDE_BIRTHDAY,
            blocks.PREF_MUTE_UPCOMING,
        }

    def test_unticking_mute_brings_the_reminders_back(self, session):
        """The mute button is one-way, so the modal is the only way back."""
        add_member(session, "U_QUIET", muted=True)
        body, view = modal_submission(
            user_id="U_QUIET", month="3", day="15", prefs=[]
        )

        views.save_birthday(
            ack=Ack(), body=body, view=view, client=FakeClient()
        )

        assert members_repo.muted_ids(session) == set()

    def test_muting_from_the_modal_works_too(self, session):
        add_member(session, "U_QUIET")
        body, view = modal_submission(
            user_id="U_QUIET",
            month="3",
            day="15",
            prefs=[blocks.PREF_MUTE_UPCOMING],
        )

        views.save_birthday(
            ack=Ack(), body=body, view=view, client=FakeClient()
        )

        assert members_repo.muted_ids(session) == {"U_QUIET"}


class TestAMemberTheRosterHasNotSeen:
    """team_join can go missing, and the repair sweep only runs at 03:00.

    Every table a member owns has a foreign key to `member`, and foreign keys
    are enforced, so without a row of their own each of these fails with an
    IntegrityError and the member gets a dead modal.
    """

    def test_they_can_still_save_a_birthday(self, session):
        body, view = modal_submission(user_id="U_GHOST", month="3", day="15")

        views.save_birthday(
            ack=Ack(), body=body, view=view, client=FakeClient()
        )

        assert birthdays_repo.get(session, "U_GHOST") is not None
        assert members_repo.get(session, "U_GHOST").display_name == "ana"

    def test_they_can_still_opt_out(self, session):
        actions.never_ask(
            body=button_press(user_id="U_GHOST"), client=FakeClient()
        )

        assert asks_repo.get(session, "U_GHOST").status == AskStatus.DECLINED

    def test_they_can_still_mute(self, session):
        actions.mute_upcoming(
            body=button_press(user_id="U_GHOST"), client=FakeClient()
        )

        assert members_repo.muted_ids(session) == {"U_GHOST"}

    def test_a_member_the_roster_does_know_keeps_their_real_name(self, session):
        """The stand-in name must never overwrite the one the sync fetched."""
        add_member(session, "U_ANA", name="Ana Kovalenko")
        body, view = modal_submission(user_id="U_ANA", month="3", day="15")

        views.save_birthday(
            ack=Ack(), body=body, view=view, client=FakeClient()
        )

        assert (
            members_repo.get(session, "U_ANA").display_name == "Ana Kovalenko"
        )


class TestJoining:
    def test_a_new_member_is_recorded_and_queued(self, session):
        configure(session, ask_delay_days=3)

        events.on_team_join(
            {"id": "U_NEW", "profile": {"display_name": "Newbie"}}
        )

        assert members_repo.get(session, "U_NEW").display_name == "Newbie"
        assert asks_repo.get(session, "U_NEW").status == AskStatus.PENDING

    def test_a_joining_bot_is_ignored(self, session):
        events.on_team_join({"id": "B_BOT", "is_bot": True, "profile": {}})

        assert members_repo.get(session, "B_BOT") is None

    def test_a_renamed_member_is_updated(self, session):
        add_member(session, "U_ANA", name="Ana")

        events.on_user_change(
            {"id": "U_ANA", "profile": {"display_name": "Ana Renamed"}}
        )

        assert members_repo.get(session, "U_ANA").display_name == "Ana Renamed"

    def test_a_deactivated_member_stops_being_active(self, session):
        add_member(session, "U_GONE")

        events.on_user_change({"id": "U_GONE", "deleted": True, "profile": {}})

        assert members_repo.active(session) == []
