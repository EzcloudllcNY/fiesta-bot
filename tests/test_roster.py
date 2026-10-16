"""Keeping the local roster in step with Slack."""

from factories import add_member, configure
from fiesta.models import AskStatus
from fiesta.repo import asks as asks_repo
from fiesta.repo import members as members_repo
from fiesta.services import roster


def slack_user(user_id: str, *, name: str = "Ana", **overrides) -> dict:
    return {
        "id": user_id,
        "name": name.lower(),
        "real_name": name,
        "profile": {"display_name": name, "real_name": name},
        **overrides,
    }


class TestImportingPeople:
    def test_users_from_slack_are_stored(self, session, slack):
        slack.users = [
            slack_user("U_ANA", name="Ana"),
            slack_user("U_BEN", name="Ben"),
        ]

        result = roster.sync(session, slack)
        session.commit()

        assert result.seen == 2
        assert {m.slack_user_id for m in members_repo.active(session)} == {
            "U_ANA",
            "U_BEN",
        }

    def test_a_display_name_change_is_picked_up(self, session, slack):
        add_member(session, "U_ANA", name="Ana")
        slack.users = [slack_user("U_ANA", name="Ana Renamed")]

        roster.sync(session, slack)
        session.commit()

        assert members_repo.get(session, "U_ANA").display_name == "Ana Renamed"

    def test_bots_are_recorded_but_never_treated_as_people(
        self, session, slack
    ):
        slack.users = [slack_user("U_ANA"), slack_user("B_BOT", is_bot=True)]

        roster.sync(session, slack)
        session.commit()

        assert [m.slack_user_id for m in members_repo.active(session)] == [
            "U_ANA"
        ]

    def test_slackbot_is_recognised_as_a_bot(self, session, slack):
        """The API does not mark Slackbot is_bot, but it is not messaged."""
        slack.users = [slack_user("USLACKBOT", name="Slackbot")]

        roster.sync(session, slack)
        session.commit()

        assert members_repo.active(session) == []


class TestPeopleLeaving:
    def test_a_user_missing_from_slack_is_deactivated(self, session, slack):
        add_member(session, "U_ANA")
        add_member(session, "U_LEFT")
        slack.users = [slack_user("U_ANA")]

        result = roster.sync(session, slack)
        session.commit()

        assert result.deactivated == 1
        assert members_repo.get(session, "U_LEFT").is_deleted is True

    def test_a_user_slack_reports_as_deleted_is_deactivated(
        self, session, slack
    ):
        slack.users = [slack_user("U_GONE", deleted=True)]

        roster.sync(session, slack)
        session.commit()

        assert members_repo.active(session) == []


class TestQueueingAsks:
    """The sync is the safety net for team_join events missed in downtime."""

    def test_a_newly_seen_person_is_queued_to_be_asked(self, session, slack):
        configure(session, ask_delay_days=3)
        slack.users = [slack_user("U_NEW")]

        result = roster.sync(session, slack)
        session.commit()

        assert result.newly_queued == 1
        assert asks_repo.get(session, "U_NEW").status == AskStatus.PENDING

    def test_someone_who_already_opted_out_is_not_queued_again(
        self, session, slack
    ):
        add_member(session, "U_PRIVATE")
        asks_repo.mark_declined(session, "U_PRIVATE")
        session.commit()
        slack.users = [slack_user("U_PRIVATE")]

        result = roster.sync(session, slack)
        session.commit()

        assert result.newly_queued == 0
        assert asks_repo.get(session, "U_PRIVATE").status == AskStatus.DECLINED

    def test_bots_are_never_queued(self, session, slack):
        slack.users = [slack_user("B_BOT", is_bot=True)]

        result = roster.sync(session, slack)
        session.commit()

        assert result.newly_queued == 0

    def test_deactivated_users_are_never_queued(self, session, slack):
        slack.users = [slack_user("U_GONE", deleted=True)]

        result = roster.sync(session, slack)
        session.commit()

        assert result.newly_queued == 0


class TestPreferencesSurvive:
    def test_a_sync_does_not_clear_a_mute(self, session, slack):
        """Preferences have their own table precisely so this can't happen."""
        add_member(session, "U_QUIET", muted=True)
        slack.users = [slack_user("U_QUIET")]

        roster.sync(session, slack)
        session.commit()

        assert members_repo.muted_ids(session) == {"U_QUIET"}


class TestDisplayName:
    def test_the_display_name_is_preferred(self, session):
        user = {
            "id": "U1",
            "profile": {"display_name": "ana", "real_name": "Ana Smith"},
        }

        assert roster.display_name(user) == "ana"

    def test_it_falls_back_through_slacks_usual_ladder(self, session):
        user = {
            "id": "U1",
            "profile": {"display_name": "", "real_name": "Ana Smith"},
        }

        assert roster.display_name(user) == "Ana Smith"

    def test_the_user_id_is_the_last_resort(self, session):
        assert roster.display_name({"id": "U1", "profile": {}}) == "U1"
