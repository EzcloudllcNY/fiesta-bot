"""Configuration, clock arithmetic, and the wiring that holds the app."""

from datetime import UTC, date, datetime

import pytest
from slack_bolt import App

from factories import add_birthday, add_member
from fiesta import clock
from fiesta.config import load_config
from fiesta.repo import birthdays as birthdays_repo
from fiesta.slack.handlers import register_all


class TestConfig:
    def test_it_reads_the_tokens_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")

        config = load_config()

        assert config.slack_bot_token == "xoxb-test"

    def test_a_missing_token_is_reported_by_name(self, monkeypatch):
        """A bot started without credentials fails later and less clearly."""
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)

        with pytest.raises(RuntimeError, match="SLACK_APP_TOKEN"):
            load_config()

    def test_database_only_commands_do_not_need_slack_credentials(
        self, monkeypatch
    ):
        """`fiesta migrate` has to work before any Slack app exists."""
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)

        config = load_config(require_slack=False)

        assert config.slack_bot_token == ""

    def test_the_database_url_points_at_the_configured_path(self, monkeypatch):
        monkeypatch.setenv("FIESTA_DB_PATH", "/tmp/somewhere.db")

        assert (
            load_config(require_slack=False).database_url
            == "sqlite:////tmp/somewhere.db"
        )


class TestClock:
    def test_today_follows_the_given_timezone(self, frozen_clock):
        """22:00 UTC is already tomorrow in Auckland."""
        frozen_clock.set(datetime(2026, 3, 10, 22, 0, tzinfo=UTC))

        assert clock.today_in("UTC") == date(2026, 3, 10)
        assert clock.today_in("Pacific/Auckland") == date(2026, 3, 11)

    def test_a_local_posting_time_converts_to_the_right_instant(self):
        moment = clock.local_time_to_utc(
            date(2026, 3, 10), "10:00", "Europe/Kyiv"
        )

        assert moment == datetime(
            2026, 3, 10, 8, 0, tzinfo=UTC
        )  # Kyiv is UTC+2

    def test_the_conversion_accounts_for_daylight_saving(self):
        """Kyiv is UTC+2 in winter and UTC+3 in summer."""
        summer = clock.local_time_to_utc(
            date(2026, 7, 1), "10:00", "Europe/Kyiv"
        )

        assert summer == datetime(2026, 7, 1, 7, 0, tzinfo=UTC)


class TestHidingABirthday:
    def test_a_birthday_can_be_hidden_and_shown_again(self, session):
        add_member(session, "U_SHY")
        add_birthday(session, "U_SHY", (6, 1))

        birthdays_repo.set_hidden(session, "U_SHY", True)
        session.commit()
        assert birthdays_repo.falling_on(session, date(2026, 6, 1)) == []

        birthdays_repo.set_hidden(session, "U_SHY", False)
        session.commit()
        assert len(birthdays_repo.falling_on(session, date(2026, 6, 1))) == 1

    def test_hiding_a_birthday_that_is_not_there_does_nothing(self, session):
        add_member(session, "U_ANA")

        assert birthdays_repo.set_hidden(session, "U_ANA", True) is None


class TestSlackRegistration:
    """A handler that fails to register shows up only as a dead button."""

    @pytest.fixture
    def bolt_app(self) -> App:
        app = App(
            token="xoxb-test",
            signing_secret="secret",
            token_verification_enabled=False,
            request_verification_enabled=False,
        )
        register_all(app)
        return app

    def test_every_interaction_has_a_handler(self, bolt_app):
        registered = {
            listener.ack_function.__name__ for listener in bolt_app._listeners
        }

        assert registered == {
            "_on_command",  # /birthday
            "_on_team_join",
            "_on_user_change",
            "_on_set",  # "Set my birthday"
            "_on_never_ask",
            "_on_mute",
            "_on_delete",  # "Remove my birthday"
            "_on_submit",  # the modal
        }


class TestScheduler:
    """The jobs exist and run when the settings say they should."""

    def test_all_four_jobs_are_registered(self, session, slack):
        from fiesta.scheduler import build_scheduler

        scheduler = build_scheduler(slack)

        assert {job.id for job in scheduler.get_jobs()} == {
            "daily_tick",
            "drain_outbox",
            "process_asks",
            "sync_roster",
        }

    def test_the_daily_job_runs_at_the_configured_local_time(
        self, session, slack
    ):
        from factories import configure
        from fiesta.scheduler import build_scheduler

        configure(session, post_time="08:30", timezone="Europe/Kyiv")

        scheduler = build_scheduler(slack)

        trigger = scheduler.get_job("daily_tick").trigger
        fields = {field.name: str(field) for field in trigger.fields}
        assert (fields["hour"], fields["minute"]) == ("8", "30")
        assert str(trigger.timezone) == "Europe/Kyiv"

    def test_the_drain_job_sends_nothing_while_auto_send_is_off(
        self, session, slack
    ):
        """The queue keeps filling.

        It just waits for the switch or `fiesta send-due`.
        """
        from factories import configure
        from fiesta.models import MessageKind
        from fiesta.repo import outbox as outbox_repo
        from fiesta.scheduler import build_scheduler

        outbox_repo.enqueue(
            session,
            kind=MessageKind.UPCOMING,
            channel="U_ANA",
            blocks=[],
            fallback_text="hi",
            dedupe_key="test:1",
        )
        configure(session, auto_send_enabled=False)
        drain_job = build_scheduler(slack).get_job("drain_outbox")

        drain_job.func()

        assert slack.sent == []
        assert len(outbox_repo.due(session)) == 1

    def test_the_drain_job_cannot_overlap_itself(self, session, slack):
        """It paces sends by sleeping; a second copy would double the rate."""
        from fiesta.scheduler import build_scheduler

        scheduler = build_scheduler(slack)

        assert scheduler.get_job("drain_outbox").max_instances == 1
