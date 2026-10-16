"""The command line, driven the way an admin would."""

import pytest
from sqlmodel import Session
from typer.testing import CliRunner

from factories import add_birthday, add_member, add_team, configure
from fiesta.cli import app
from fiesta.db import make_engine

runner = CliRunner()


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """Run commands against a real, throwaway database file.

    A file rather than the in-memory fixture, because each command initialises
    its own engine — which is exactly the path being tested.
    """
    monkeypatch.setenv("FIESTA_DB_PATH", str(tmp_path / "fiesta.db"))
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)

    def invoke(*args: str):
        result = runner.invoke(app, list(args))
        if result.exception and not isinstance(result.exception, SystemExit):
            raise result.exception
        return result

    invoke("migrate")
    return invoke


class TestMigrate:
    def test_migrating_creates_a_usable_database(self, cli):
        """`fiesta migrate` has to work before any Slack credentials exist."""
        result = cli("settings", "list")

        assert result.exit_code == 0
        assert "celebration_channel" in result.output

    def test_migrating_twice_is_harmless(self, cli):
        assert cli("migrate").exit_code == 0


class TestSettings:
    def test_a_setting_can_be_changed_and_read_back(self, cli):
        cli("settings", "set", "post_time", "08:30")

        assert cli("settings", "get", "post_time").output.strip() == "08:30"

    def test_a_bare_word_does_not_need_quoting(self, cli):
        """`set timezone Europe/Berlin` is what someone will actually type."""
        cli("settings", "set", "timezone", "Europe/Berlin")

        assert (
            cli("settings", "get", "timezone").output.strip() == "Europe/Berlin"
        )

    def test_the_list_explains_every_setting(self, cli):
        output = cli("settings", "list").output

        assert "upcoming_days_before" in output
        assert "heads-up" in output
        assert "asks_enabled" in output

    def test_a_misspelled_setting_is_refused_with_the_valid_names(self, cli):
        result = cli("settings", "set", "post_tiem", "08:30")

        assert result.exit_code == 1
        assert "post_time" in result.output

    def test_a_value_the_bot_cannot_use_is_refused_and_not_stored(self, cli):
        result = cli("settings", "set", "post_time", "half past nine")

        assert result.exit_code == 1
        assert (
            "09:30" in result.output
        )  # the error shows what a good value looks like
        assert cli("settings", "get", "post_time").output.strip() == "10:00"

    def test_a_setting_can_be_put_back_to_its_default(self, cli):
        cli("settings", "set", "max_asks", "5")

        cli("settings", "reset", "max_asks")

        assert cli("settings", "get", "max_asks").output.strip() == "3"

    def test_a_change_the_bot_only_reads_at_startup_says_so(self, cli):
        assert "restart" in cli("settings", "set", "post_time", "08:30").output
        assert "restart" not in cli("settings", "set", "max_asks", "5").output


class TestFeatureSwitches:
    def test_a_feature_can_be_switched_off_and_on_again(self, cli):
        cli("settings", "set", "auto_send_enabled", "false")
        assert (
            cli("settings", "get", "auto_send_enabled").output.strip()
            == "false"
        )

        cli("settings", "set", "auto_send_enabled", "true")
        assert (
            cli("settings", "get", "auto_send_enabled").output.strip() == "true"
        )

    def test_a_tick_with_celebrations_off_says_so(self, cli):
        cli("settings", "set", "celebration_enabled", "false")

        assert (
            "celebration   disabled"
            in cli("tick", "--date", "2026-03-10").output
        )


class TestTemplates:
    def test_migrating_adds_no_texts(self, cli):
        """Migrations build tables only; texts are the admin's to add."""
        output = cli("templates", "list").output

        assert "{user}" not in output

    def test_a_text_can_be_added(self, cli):
        result = cli("templates", "add", "Many happy returns {user}")

        assert "Added template 1" in result.output
        assert "Many happy returns" in cli("templates", "list").output

    def test_a_text_without_its_placeholder_is_refused(self, cli):
        result = cli("templates", "add", "no placeholder")

        assert result.exit_code == 1

    def test_a_text_can_be_reworded(self, cli):
        cli("templates", "add", "Old words {user}")

        cli("templates", "edit", "1", "New words {user}")

        output = cli("templates", "list").output
        assert "New words" in output
        assert "Old words" not in output

    def test_a_rewording_without_the_placeholder_is_refused(self, cli):
        cli("templates", "add", "Old words {user}")

        assert cli("templates", "edit", "1", "no placeholder").exit_code == 1
        assert "Old words" in cli("templates", "list").output

    def test_a_text_can_be_switched_off_and_on(self, cli):
        cli("templates", "add", "Hi {user}")

        cli("templates", "edit", "1", "--disabled")
        assert "false" in cli("templates", "list").output

        cli("templates", "edit", "1", "--enabled")
        assert "true" in cli("templates", "list").output

    def test_an_edit_with_nothing_to_change_is_refused(self, cli):
        cli("templates", "add", "Hi {user}")

        assert cli("templates", "edit", "1").exit_code == 1

    def test_a_text_can_be_removed(self, cli):
        cli("templates", "add", "Hi {user}")

        cli("templates", "remove", "1")

        assert "Hi" not in cli("templates", "list").output

    def test_an_unknown_template_id_is_refused(self, cli):
        assert cli("templates", "remove", "99").exit_code == 1
        assert cli("templates", "edit", "99", "--disabled").exit_code == 1


class TestGifs:
    URL = "https://example.test/party.gif"

    def test_a_gif_can_be_added_and_listed(self, cli):
        cli("gifs", "add", self.URL, "--tags", "cake")

        output = cli("gifs", "list").output
        assert "party.gif" in output
        assert "cake" in output

    def test_the_same_url_cannot_be_added_twice(self, cli):
        cli("gifs", "add", self.URL)

        result = cli("gifs", "add", self.URL)

        assert result.exit_code == 1
        assert "already" in result.output

    def test_a_gif_can_be_edited(self, cli):
        cli("gifs", "add", self.URL, "--tags", "cake")

        cli(
            "gifs",
            "edit",
            "1",
            "--url",
            "https://example.test/other.gif",
            "--tags",
            "",
            "--disabled",
        )

        output = cli("gifs", "list").output
        assert "other.gif" in output
        assert "cake" not in output
        assert "false" in output

    def test_a_gif_cannot_take_a_url_already_in_the_pool(self, cli):
        cli("gifs", "add", self.URL)
        cli("gifs", "add", "https://example.test/other.gif")

        assert cli("gifs", "edit", "2", "--url", self.URL).exit_code == 1

    def test_an_edit_with_nothing_to_change_is_refused(self, cli):
        cli("gifs", "add", self.URL)

        assert cli("gifs", "edit", "1").exit_code == 1

    def test_a_gif_can_be_removed(self, cli):
        cli("gifs", "add", self.URL)

        cli("gifs", "remove", "1")

        assert "party.gif" not in cli("gifs", "list").output

    def test_an_unknown_gif_id_is_refused(self, cli):
        assert cli("gifs", "remove", "99").exit_code == 1


class TestTick:
    def test_a_tick_reports_what_it_queued(self, cli):
        result = cli("tick", "--date", "2026-03-10")

        assert result.exit_code == 0
        assert "2026-03-10" in result.output

    def test_a_tick_says_why_each_step_queued_nothing(self, cli):
        """A bare "0 queued" can't tell switched off from a quiet day."""
        cli("settings", "set", "upcoming_enabled", "false")

        output = cli("tick", "--date", "2026-03-10").output

        assert "upcoming DMs  disabled" in output
        assert "upcoming_enabled is false" in output
        assert "celebration   not_configured" in output

    def test_a_tick_reminds_to_send_when_auto_send_is_off(self, cli, tmp_path):
        with Session(make_engine(f"sqlite:///{tmp_path / 'fiesta.db'}")) as db:
            add_team(db, "U_ANA", "U_BEN")
            add_birthday(db, "U_ANA", (3, 10))
            configure(
                db, celebration_channel="C_GENERAL", auto_send_enabled=False
            )

        output = cli("tick", "--date", "2026-03-10").output

        assert "celebration   queued" in output
        assert "fiesta send-due" in output

    def test_a_tick_needs_no_slack_credentials(self, cli):
        """It only writes rows; delivery is a separate command."""
        assert cli("tick").exit_code == 0


class TestUsers:
    @pytest.fixture
    def db(self, cli, tmp_path):
        """The same file the commands use, for arranging the roster."""
        with Session(make_engine(f"sqlite:///{tmp_path / 'fiesta.db'}")) as db:
            yield db

    def test_the_list_shows_people_with_their_birthday_and_mute(self, cli, db):
        add_member(db, "U_ANA", name="Ana")
        add_member(db, "U_BEN", name="Ben", muted=True)
        add_birthday(db, "U_ANA", (3, 10))

        output = cli("users", "list").output

        assert "U_ANA" in output
        assert "03-10" in output
        assert "true" in output  # Ben is muted
        assert "2 member(s)" in output

    def test_the_list_leaves_out_bots_and_leavers_unless_asked(self, cli, db):
        add_member(db, "U_ANA")
        add_member(db, "U_BOT", is_bot=True)
        add_member(db, "U_GONE", is_deleted=True)

        assert "U_BOT" not in cli("users", "list").output

        output = cli("users", "list", "--all").output
        assert "U_BOT" in output
        assert "deactivated" in output

    def test_the_list_never_shows_the_birth_year(self, cli, db):
        add_member(db, "U_ANA")
        add_birthday(db, "U_ANA", (3, 10), year=1990, hidden=True)

        output = cli("users", "list").output

        assert "1990" not in output
        assert "03-10 (hidden)" in output

    def test_one_member_can_be_looked_at_in_detail(self, cli, db):
        add_member(db, "U_ANA", name="Ana")
        add_birthday(db, "U_ANA", (3, 10))

        output = cli("users", "show", "U_ANA").output

        assert "Ana" in output
        assert "03-10" in output

    def test_an_unknown_id_is_refused(self, cli):
        result = cli("users", "show", "U_NOBODY")

        assert result.exit_code == 1
        assert "users list" in result.output

    def test_members_can_be_muted_and_unmuted_by_id(self, cli, db):
        add_team(db, "U_ANA", "U_BEN")

        cli("users", "mute", "U_ANA", "U_BEN")
        assert "2 member(s)" in cli("users", "unmute", "U_ANA", "U_BEN").output

        assert "true" not in cli("users", "list").output

    def test_everyone_but_a_tester_can_be_muted_and_it_undoes_cleanly(
        self, cli, db
    ):
        """The local-testing flow: only one person gets the heads-up."""
        add_team(db, "U_ANA", "U_BEN", "U_CAT")

        assert "Muted 2" in cli("users", "mute", "--all-except", "U_ANA").output
        assert "false" in cli("users", "show", "U_ANA").output

        assert "Unmuted 2" in cli("users", "unmute", "--all").output
        assert "true" not in cli("users", "list").output

    def test_a_mute_with_an_unknown_id_changes_nothing(self, cli, db):
        add_member(db, "U_ANA")

        result = cli("users", "mute", "U_ANA", "U_NOBODY")

        assert result.exit_code == 1
        assert "true" not in cli("users", "list").output

    def test_a_mute_needs_ids_or_all_except_but_not_both(self, cli, db):
        add_team(db, "U_ANA", "U_BEN")

        assert cli("users", "mute").exit_code == 1
        assert (
            cli("users", "mute", "U_BEN", "--all-except", "U_ANA").exit_code
            == 1
        )
