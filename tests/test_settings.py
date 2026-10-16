"""Admin settings and member preferences."""

import pytest

from factories import add_member
from fiesta.repo import members as members_repo
from fiesta.repo import settings as settings_repo


class TestDefaults:
    def test_an_unset_setting_falls_back_to_its_default(self, session):
        """A fresh database has no rows, so the bot must work without any."""
        assert settings_repo.get(session, "post_time") == "10:00"

    def test_a_stored_value_wins_over_the_default(self, session):
        settings_repo.set_value(session, "post_time", "08:30")
        session.commit()

        assert settings_repo.get(session, "post_time") == "08:30"

    def test_listing_merges_stored_values_over_defaults(self, session):
        settings_repo.set_value(session, "upcoming_days_before", 7)
        session.commit()

        values = settings_repo.all_values(session)

        assert values["upcoming_days_before"] == 7
        assert values["post_time"] == "10:00"

    def test_values_keep_their_type(self, session):
        """Settings are JSON-encoded, so an int comes back an int, not "3"."""
        settings_repo.set_value(session, "max_asks", 5)
        session.commit()

        assert settings_repo.get(session, "max_asks") == 5


class TestTypos:
    """An unknown key is a mistake, not a new setting."""

    def test_reading_an_unknown_setting_raises(self, session):
        with pytest.raises(settings_repo.UnknownSetting):
            settings_repo.get(session, "post_tiem")

    def test_writing_an_unknown_setting_raises(self, session):
        with pytest.raises(settings_repo.UnknownSetting):
            settings_repo.set_value(session, "celebration_chanel", "C_GENERAL")


class TestReset:
    def test_resetting_brings_the_default_back(self, session):
        settings_repo.set_value(session, "post_time", "08:30")
        session.commit()

        assert settings_repo.reset(session, "post_time") is True
        session.commit()

        assert settings_repo.get(session, "post_time") == "10:00"

    def test_resetting_an_unchanged_setting_reports_nothing_to_do(
        self, session
    ):
        assert settings_repo.reset(session, "post_time") is False

    def test_resetting_an_unknown_setting_raises(self, session):
        with pytest.raises(settings_repo.UnknownSetting):
            settings_repo.reset(session, "post_tiem")


class TestParsing:
    """Admin input is checked before it is stored, not when the bot trips."""

    @pytest.mark.parametrize(
        ("key", "typed", "stored"),
        [
            ("celebration_channel", "C0123456789", "C0123456789"),
            ("post_time", "9:30", "09:30"),
            ("timezone", "Europe/Berlin", "Europe/Berlin"),
            ("max_asks", "5", 5),
            ("ask_delay_days", "0", 0),
            ("gif_provider", "db", "db"),
            ("asks_enabled", "false", False),
            ("asks_enabled", "True", True),
        ],
    )
    def test_a_sensible_value_is_accepted(self, key, typed, stored):
        assert settings_repo.parse(key, typed) == stored

    @pytest.mark.parametrize(
        ("key", "typed"),
        [
            ("celebration_channel", "#general"),
            ("post_time", "25:00"),
            ("post_time", "noon"),
            ("timezone", "Kyiv"),
            ("max_asks", "three"),
            ("max_asks", "0"),
            ("gif_provider", "giphy"),
            ("auto_send_enabled", "maybe"),
            ("auto_send_enabled", "off"),
        ],
    )
    def test_a_value_the_bot_cannot_use_is_refused(self, key, typed):
        with pytest.raises(settings_repo.InvalidValue):
            settings_repo.parse(key, typed)


class TestFeatureSwitches:
    def test_every_feature_is_on_in_a_fresh_database(self, session):
        """Switches exist to turn things off; a new install should just work."""
        switches = [
            key for key in settings_repo.SPECS if key.endswith("_enabled")
        ]

        assert switches
        assert all(settings_repo.get(session, key) is True for key in switches)


class TestMutePreference:
    def test_a_member_starts_unmuted(self, session):
        add_member(session, "U_ANA")

        assert members_repo.muted_ids(session) == set()

    def test_muting_is_remembered(self, session):
        add_member(session, "U_ANA")

        members_repo.set_muted_upcoming(session, "U_ANA", True)
        session.commit()

        assert members_repo.muted_ids(session) == {"U_ANA"}

    def test_muting_can_be_undone(self, session):
        add_member(session, "U_ANA", muted=True)

        members_repo.set_muted_upcoming(session, "U_ANA", False)
        session.commit()

        assert members_repo.muted_ids(session) == set()
