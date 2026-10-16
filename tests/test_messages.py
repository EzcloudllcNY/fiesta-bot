"""What the messages actually say."""

import pytest

from fiesta.repo import templates as templates_repo
from fiesta.services import templates as templates_service
from fiesta.slack import blocks


class TestMentions:
    """Slack resolves <@U123> itself, so names can never go stale."""

    def test_a_mention_is_the_slack_user_reference(self):
        assert blocks.mention("U_ANA") == "<@U_ANA>"

    def test_mentions_are_placed_in_mrkdwn_not_plain_text(self):
        """In a plain_text field Slack would print <@U123> literally."""
        (section,) = blocks.celebration_blocks(text="hi <@U_ANA>")

        assert section["text"]["type"] == "mrkdwn"


class TestCongratulations:
    def test_the_placeholder_becomes_a_mention(self, session):
        templates_repo.add(session, text="Hooray: {user}")
        session.commit()

        assert templates_service.render(session, "U_ANA") == "Hooray: <@U_ANA>"

    def test_a_disabled_template_is_not_used(self, session):
        template = templates_repo.add(session, text="Disabled: {user}")
        session.commit()
        templates_repo.update(session, template, enabled=False)
        session.commit()

        assert "Disabled" not in templates_service.render(session, "U_ANA")

    def test_an_empty_template_table_still_produces_a_message(self, session):
        """Deleting every text must never skip a celebration."""
        rendered = templates_service.render(session, "U_ANA")

        assert "<@U_ANA>" in rendered

    def test_a_template_must_contain_its_placeholder(self, session):
        with pytest.raises(ValueError, match=r"\{user\}"):
            templates_repo.add(session, text="no placeholder here")


class TestAskMessage:
    def test_a_new_joiner_is_welcomed(self):
        rendered = str(
            blocks.ask_blocks(tone=blocks.AskTone.WELCOME, asks_left=2)
        )

        assert "Welcome aboard" in rendered

    def test_the_rollout_says_the_bot_is_the_new_arrival(self):
        """Said to a team here for years, "welcome aboard" reads wrong."""
        rendered = str(
            blocks.ask_blocks(tone=blocks.AskTone.INTRODUCTION, asks_left=2)
        )

        assert "I'm new here" in rendered
        assert "Welcome aboard" not in rendered

    def test_a_reminder_acknowledges_it_has_asked_before(self):
        rendered = str(
            blocks.ask_blocks(tone=blocks.AskTone.REMINDER, asks_left=1)
        )

        assert "Still no birthday" in rendered

    def test_every_tone_offers_both_a_way_in_and_a_way_out(self):
        for tone in blocks.AskTone:
            (_, actions, _) = blocks.ask_blocks(tone=tone, asks_left=2)

            action_ids = [
                element["action_id"] for element in actions["elements"]
            ]
            assert action_ids == [
                blocks.ACTION_SET_BIRTHDAY,
                blocks.ACTION_NEVER_ASK,
            ]

    def test_the_ask_says_the_year_stays_private(self):
        """People share a year more readily when they see it won't be shown."""
        assert "private" in str(
            blocks.ask_blocks(tone=blocks.AskTone.WELCOME, asks_left=2)
        )


class TestBirthdayModal:
    def test_an_empty_modal_preselects_nothing(self):
        modal = blocks.birthday_modal()

        month_block = modal["blocks"][0]
        assert "initial_option" not in month_block["element"]

    def test_an_existing_birthday_is_preselected(self, session):
        """Changing one field shouldn't mean re-entering the others."""
        modal = blocks.birthday_modal(current=(3, 15, 1990))

        month, day, year = modal["blocks"][:3]
        assert month["element"]["initial_option"]["text"]["text"] == "March"
        assert day["element"]["initial_option"]["value"] == "15"
        assert year["element"]["initial_value"] == "1990"

    def test_a_birthday_without_a_year_leaves_the_year_blank(self):
        modal = blocks.birthday_modal(current=(3, 15, None))

        year_block = modal["blocks"][2]
        assert "initial_value" not in year_block["element"]

    def test_the_year_is_optional(self):
        year_block = blocks.birthday_modal()["blocks"][2]

        assert year_block["optional"] is True

    def test_the_privacy_switches_start_unticked_for_a_new_member(self):
        """Slack rejects the view if initial_options is present but empty."""
        prefs = blocks.birthday_modal()["blocks"][3]["element"]

        assert "initial_options" not in prefs

    def test_a_hidden_birthday_shows_its_switch_already_ticked(self):
        prefs = blocks.birthday_modal(current=(3, 15, None), hidden=True)[
            "blocks"
        ][3]

        values = [
            option["value"] for option in prefs["element"]["initial_options"]
        ]
        assert values == [blocks.PREF_HIDE_BIRTHDAY]

    def test_a_member_with_no_birthday_is_not_offered_a_delete(self):
        """There is nothing to remove yet, so the button would only confuse."""
        assert blocks.ACTION_DELETE_BIRTHDAY not in str(
            blocks.birthday_modal()["blocks"]
        )

    def test_an_existing_birthday_can_be_removed_from_the_same_view(self):
        delete_block = blocks.birthday_modal(current=(3, 15, None))["blocks"][
            -1
        ]

        (button,) = delete_block["elements"]
        assert button["action_id"] == blocks.ACTION_DELETE_BIRTHDAY
        assert button["style"] == "danger"


class TestUpcomingMessage:
    def test_it_names_the_person_and_the_date(self):
        rendered = str(
            blocks.upcoming_blocks(
                birthday_user_id="U_ANA", days_away=3, month=3, day=13
            )
        )

        assert "<@U_ANA>" in rendered
        assert "March 13" in rendered

    def test_it_offers_a_way_to_mute(self):
        (_, actions) = blocks.upcoming_blocks(
            birthday_user_id="U_ANA", days_away=3, month=3, day=13
        )

        assert (
            actions["elements"][0]["action_id"] == blocks.ACTION_MUTE_UPCOMING
        )


class TestCelebrationMessage:
    def test_without_a_gif_it_is_just_the_text(self):
        result = blocks.celebration_blocks(text="Happy birthday!")

        assert [block["type"] for block in result] == ["section"]

    def test_with_a_gif_the_image_comes_last(self):
        """Text first, so the message still reads right if the image fails."""
        result = blocks.celebration_blocks(
            text="Happy birthday!",
            gif_url="https://example.test/a.gif",
        )

        assert [block["type"] for block in result] == ["section", "image"]
