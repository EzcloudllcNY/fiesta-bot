"""The daily job, and catching up on days the bot was down for."""

from datetime import UTC, date, datetime

from factories import add_birthday, add_member, add_team, configure
from fiesta.repo import job_runs as job_runs_repo
from fiesta.repo import outbox as outbox_repo
from fiesta.services import daily

CHANNEL = "C_GENERAL"


def at_kyiv_midday(day: date) -> datetime:
    """A UTC instant in the middle of ``day`` in the workspace timezone."""
    return datetime(day.year, day.month, day.day, 10, 0, tzinfo=UTC)


class TestOneDay:
    def test_a_tick_queues_both_the_heads_up_and_the_celebration(self, session):
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 10))  # today
        add_birthday(session, "U_BEN", (3, 13))  # three days out
        configure(session, celebration_channel=CHANNEL, upcoming_days_before=3)

        result = daily.run(session, date(2026, 3, 10))

        assert result.celebration_queued is True
        assert result.upcoming.queued == 1

    def test_a_quiet_day_queues_nothing(self, session):
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (9, 1))
        configure(session, celebration_channel=CHANNEL)

        result = daily.run(session, date(2026, 3, 10))

        assert result.upcoming.queued == 0
        assert result.celebration_queued is False

    def test_a_tick_records_the_day_it_handled(self, session):
        configure(session, celebration_channel=CHANNEL)

        daily.run(session, date(2026, 3, 10))

        assert job_runs_repo.last_run_on(session, daily.JOB_NAME) == date(
            2026, 3, 10
        )

    def test_running_the_same_day_twice_queues_nothing_extra(self, session):
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 10))
        configure(session, celebration_channel=CHANNEL)

        daily.run(session, date(2026, 3, 10))
        before = len(outbox_repo.due(session))
        daily.run(session, date(2026, 3, 10))

        assert len(outbox_repo.due(session)) == before


class TestCatchUp:
    """A bot that was down over someone's birthday should still post, late."""

    def test_a_first_ever_start_handles_today_only(self, session, frozen_clock):
        """Replaying history on a fresh install would announce old birthdays."""
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (3, 5))  # five days ago
        configure(session, celebration_channel=CHANNEL)
        frozen_clock.set(at_kyiv_midday(date(2026, 3, 10)))

        results = daily.catch_up(session)

        assert [r.day for r in results] == [date(2026, 3, 10)]
        assert outbox_repo.due(session) == []

    def test_a_missed_day_is_replayed(self, session, frozen_clock):
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (3, 9))
        configure(session, celebration_channel=CHANNEL)
        job_runs_repo.record(session, daily.JOB_NAME, date(2026, 3, 8))
        frozen_clock.set(at_kyiv_midday(date(2026, 3, 10)))

        results = daily.catch_up(session)

        assert [r.day for r in results] == [date(2026, 3, 9), date(2026, 3, 10)]
        assert [m.dedupe_key for m in outbox_repo.due(session)] == [
            "celebration:2026-03-09:U_ANA"
        ]

    def test_nothing_is_replayed_when_today_is_already_done(
        self, session, frozen_clock
    ):
        configure(session, celebration_channel=CHANNEL)
        job_runs_repo.record(session, daily.JOB_NAME, date(2026, 3, 10))
        frozen_clock.set(at_kyiv_midday(date(2026, 3, 10)))

        assert daily.catch_up(session) == []

    def test_catch_up_stops_after_a_week(self, session, frozen_clock):
        """Older than that, stale messages confuse more than they help."""
        configure(session, celebration_channel=CHANNEL)
        job_runs_repo.record(session, daily.JOB_NAME, date(2025, 1, 1))
        frozen_clock.set(at_kyiv_midday(date(2026, 3, 10)))

        results = daily.catch_up(session)

        assert len(results) == daily.MAX_CATCHUP_DAYS
        assert results[-1].day == date(2026, 3, 10)

    def test_replaying_does_not_duplicate_what_already_went_out(
        self, session, frozen_clock
    ):
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 10))
        configure(session, celebration_channel=CHANNEL)
        frozen_clock.set(at_kyiv_midday(date(2026, 3, 10)))
        daily.run(session, date(2026, 3, 10))

        job_runs_repo.record(session, daily.JOB_NAME, date(2026, 3, 8))
        daily.catch_up(session)

        celebrations = [
            m
            for m in outbox_repo.due(session)
            if m.dedupe_key.startswith("celebration")
        ]
        assert len(celebrations) == 1

    def test_a_stale_heads_up_is_not_replayed(self, session, frozen_clock):
        """Nobody wants to get ready for a party that already happened."""
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 9))  # yesterday
        configure(session, celebration_channel=CHANNEL, upcoming_days_before=3)
        job_runs_repo.record(session, daily.JOB_NAME, date(2026, 3, 5))
        frozen_clock.set(at_kyiv_midday(date(2026, 3, 10)))

        daily.catch_up(session)

        kinds = {m.kind for m in outbox_repo.due(session)}
        assert kinds == {
            "celebration"
        }  # the late congratulation still goes out

    def test_a_heads_up_is_replayed_if_still_ahead(self, session, frozen_clock):
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 12))
        configure(session, celebration_channel=CHANNEL, upcoming_days_before=3)
        job_runs_repo.record(session, daily.JOB_NAME, date(2026, 3, 8))
        frozen_clock.set(at_kyiv_midday(date(2026, 3, 10)))

        daily.catch_up(session)

        (message,) = outbox_repo.due(session)
        assert message.channel == "U_BEN"
        # Counted from now, not from the day being replayed: the 12th is two
        # days off.
        assert "in 2 days" in str(message.blocks)

    def test_a_day_with_no_channel_set_is_not_counted_as_handled(
        self, session, frozen_clock
    ):
        """Otherwise the birthday is buried.

        Catch-up only revisits days after the last one.
        """
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 10))
        frozen_clock.set(at_kyiv_midday(date(2026, 3, 10)))

        daily.run(session, date(2026, 3, 10))  # celebration_channel is unset

        assert job_runs_repo.last_run_on(session, daily.JOB_NAME) is None

    def test_setting_the_channel_later_still_gets_the_post_out(
        self, session, frozen_clock
    ):
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 10))
        frozen_clock.set(at_kyiv_midday(date(2026, 3, 10)))
        daily.run(session, date(2026, 3, 10))

        configure(session, celebration_channel=CHANNEL)
        daily.catch_up(session)

        assert [m.dedupe_key for m in outbox_repo.due(session)] == [
            "celebration:2026-03-10:U_ANA"
        ]

    def test_a_quiet_day_with_a_channel_set_counts_as_handled(
        self, session, frozen_clock
    ):
        configure(session, celebration_channel=CHANNEL)
        frozen_clock.set(at_kyiv_midday(date(2026, 3, 10)))

        daily.run(session, date(2026, 3, 10))

        assert job_runs_repo.last_run_on(session, daily.JOB_NAME) == date(
            2026, 3, 10
        )

    def test_a_day_with_posts_switched_off_counts_as_handled(
        self, session, frozen_clock
    ):
        """Switching posts back on must not flush late birthdays out."""
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 10))
        configure(
            session, celebration_channel=CHANNEL, celebration_enabled=False
        )
        frozen_clock.set(at_kyiv_midday(date(2026, 3, 10)))
        daily.run(session, date(2026, 3, 10))

        configure(session, celebration_enabled=True)
        daily.catch_up(session)

        assert job_runs_repo.last_run_on(session, daily.JOB_NAME) == date(
            2026, 3, 10
        )
        assert outbox_repo.due(session) == []

    def test_the_recorded_day_never_moves_backwards(self, session):
        """Replaying an older day must not make the next catch-up repeat."""
        job_runs_repo.record(session, daily.JOB_NAME, date(2026, 3, 10))
        job_runs_repo.record(session, daily.JOB_NAME, date(2026, 3, 8))

        assert job_runs_repo.last_run_on(session, daily.JOB_NAME) == date(
            2026, 3, 10
        )


class TestTimezone:
    """ "Today" means today in the workspace timezone, not on the server."""

    def test_the_day_follows_the_configured_timezone(
        self, session, frozen_clock
    ):
        configure(
            session, celebration_channel=CHANNEL, timezone="Pacific/Auckland"
        )
        # 22:00 UTC on the 10th is already the 11th in Auckland.
        frozen_clock.set(datetime(2026, 3, 10, 22, 0, tzinfo=UTC))

        results = daily.catch_up(session)

        assert results[0].day == date(2026, 3, 11)
