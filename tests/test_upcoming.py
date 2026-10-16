"""The private heads-up DMs sent a few days before a birthday."""

from datetime import date

from factories import add_birthday, add_member, add_team, configure
from fiesta.repo import outbox as outbox_repo
from fiesta.services import upcoming
from fiesta.services.upcoming import Outcome

TODAY = date(2026, 3, 10)


def recipients_of_upcoming_dms(session) -> set[str]:
    return {message.channel for message in outbox_repo.due(session)}


class TestWhoGetsTold:
    def test_everyone_except_the_birthday_person_is_told(self, session):
        add_team(session, "U_ANA", "U_BEN", "U_CAL")
        add_birthday(session, "U_ANA", (3, 13))
        configure(session, upcoming_days_before=3)

        upcoming.enqueue_for(session, TODAY)

        assert recipients_of_upcoming_dms(session) == {"U_BEN", "U_CAL"}

    def test_muted_members_are_not_told(self, session):
        add_team(session, "U_ANA", "U_BEN")
        add_member(session, "U_QUIET", muted=True)
        add_birthday(session, "U_ANA", (3, 13))
        configure(session, upcoming_days_before=3)

        upcoming.enqueue_for(session, TODAY)

        assert recipients_of_upcoming_dms(session) == {"U_BEN"}

    def test_a_birthday_nobody_can_hear_about_says_so(self, session):
        """Not "nothing to send": there is a birthday, just no audience."""
        add_team(session, "U_ANA")
        add_member(session, "U_QUIET", muted=True)
        add_birthday(session, "U_ANA", (3, 13))
        configure(session, upcoming_days_before=3)

        result = upcoming.enqueue_for(session, TODAY)

        assert result.outcome is Outcome.NO_RECIPIENTS

    def test_bots_and_deactivated_members_are_not_told(self, session):
        add_team(session, "U_ANA", "U_BEN")
        add_member(session, "U_ROBOT", is_bot=True)
        add_member(session, "U_GONE", is_deleted=True)
        add_birthday(session, "U_ANA", (3, 13))
        configure(session, upcoming_days_before=3)

        upcoming.enqueue_for(session, TODAY)

        assert recipients_of_upcoming_dms(session) == {"U_BEN"}

    def test_a_hidden_birthday_is_not_announced_to_anyone(self, session):
        add_team(session, "U_SHY", "U_BEN")
        add_birthday(session, "U_SHY", (3, 13), hidden=True)
        configure(session, upcoming_days_before=3)

        result = upcoming.enqueue_for(session, TODAY)

        assert result.outcome is Outcome.NOTHING_TO_SEND


class TestTiming:
    def test_nothing_is_sent_when_no_birthday_is_in_range(self, session):
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (9, 1))
        configure(session, upcoming_days_before=3)

        result = upcoming.enqueue_for(session, TODAY)

        assert result.outcome is Outcome.NOTHING_TO_SEND
        assert result.target == date(2026, 3, 13)

    def test_the_lead_time_setting_decides_the_day(self, session):
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 17))
        configure(session, upcoming_days_before=7)

        assert upcoming.enqueue_for(session, TODAY).queued == 1

    def test_the_lead_time_crosses_into_the_next_month(self, session):
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (4, 2))
        configure(session, upcoming_days_before=3)

        assert upcoming.enqueue_for(session, date(2026, 3, 30)).queued == 1

    def test_the_lead_time_crosses_into_the_next_year(self, session):
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (1, 2))
        configure(session, upcoming_days_before=3)

        assert upcoming.enqueue_for(session, date(2026, 12, 30)).queued == 1


class TestSeveralBirthdays:
    def test_two_birthdays_on_the_same_day_produce_two_separate_dms(
        self, session
    ):
        """Unlike the celebration post, these stay separate.

        Each is its own heads-up.
        """
        add_team(session, "U_ANA", "U_BEN", "U_CAL")
        add_birthday(session, "U_ANA", (3, 13))
        add_birthday(session, "U_BEN", (3, 13))
        configure(session, upcoming_days_before=3)

        result = upcoming.enqueue_for(session, TODAY)

        # Ana hears about Ben, Ben hears about Ana, Cal hears about both.
        assert result.outcome is Outcome.QUEUED
        assert result.queued == 4

    def test_a_member_is_never_told_about_their_own_birthday(self, session):
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 13))
        add_birthday(session, "U_BEN", (3, 13))
        configure(session, upcoming_days_before=3)

        upcoming.enqueue_for(session, TODAY)

        for message in outbox_repo.due(session):
            assert f"<@{message.channel}>" not in str(message.blocks)


class TestRunningTwice:
    def test_running_the_job_twice_does_not_send_twice(self, session):
        add_team(session, "U_ANA", "U_BEN", "U_CAL")
        add_birthday(session, "U_ANA", (3, 13))
        configure(session, upcoming_days_before=3)

        first = upcoming.enqueue_for(session, TODAY)
        second = upcoming.enqueue_for(session, TODAY)

        assert first.queued == 2
        assert second.outcome is Outcome.ALREADY_QUEUED
        assert second.queued == 0

    def test_next_year_is_a_separate_announcement(self, session):
        """The dedupe key carries the year, so a birthday recurs every year."""
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 13))
        configure(session, upcoming_days_before=3)

        upcoming.enqueue_for(session, TODAY)
        next_year = upcoming.enqueue_for(session, date(2027, 3, 10))

        assert next_year.queued == 1


class TestMessageContent:
    def test_the_dm_mentions_the_birthday_person(self, session):
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 13))
        configure(session, upcoming_days_before=3)

        upcoming.enqueue_for(session, TODAY)

        (message,) = outbox_repo.due(session)
        assert "<@U_ANA>" in str(message.blocks)
        assert "March 13" in str(message.blocks)

    def test_a_one_day_lead_time_says_tomorrow(self, session):
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 11))
        configure(session, upcoming_days_before=1)

        upcoming.enqueue_for(session, TODAY)

        (message,) = outbox_repo.due(session)
        assert "tomorrow" in str(message.blocks)


class TestSwitchedOff:
    def test_nobody_is_told_when_heads_ups_are_off(self, session):
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 13))
        configure(session, upcoming_days_before=3, upcoming_enabled=False)

        result = upcoming.enqueue_for(session, TODAY)

        assert result.outcome is Outcome.DISABLED
        assert recipients_of_upcoming_dms(session) == set()
