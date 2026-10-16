"""The daily job, and the catch-up that replays days the bot was down for."""

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlmodel import Session

from fiesta import clock
from fiesta.repo import job_runs as job_runs_repo
from fiesta.repo import settings as settings_repo
from fiesta.services import celebration as celebration_service
from fiesta.services import upcoming

log = logging.getLogger(__name__)

JOB_NAME = "daily_tick"
MAX_CATCHUP_DAYS = 7
"""How far back a restart will replay. Beyond a week the messages are stale
enough that posting them would confuse people more than the silence did."""


@dataclass(frozen=True, slots=True)
class TickResult:
    day: date
    upcoming: upcoming.Result
    celebration: celebration_service.Outcome

    @property
    def celebration_queued(self) -> bool:
        return self.celebration is celebration_service.Outcome.QUEUED


def run(
    session: Session, day: date, *, today: date | None = None
) -> TickResult:
    """Queue everything that belongs to ``day``.

    Safe to run repeatedly: both steps deduplicate on their own keys, so a
    replay of a day that was already handled queues nothing.

    ``today`` is only given by the catch-up, which replays days already gone. It
    lets the heads-up step tell "three days from the day being replayed" apart
    from "three days from now" — see
    :func:`fiesta.services.upcoming.enqueue_for`.
    """
    upcoming_result = upcoming.enqueue_for(session, day, today=today)
    outcome = celebration_service.enqueue_for(session, day)

    # A day skipped for want of a celebration channel is not a day that was
    # handled. Recording it would bury the birthday for good: catch-up only
    # revisits days after the last recorded one, so setting the channel later
    # would come too late.
    if outcome is not celebration_service.Outcome.NOT_CONFIGURED:
        job_runs_repo.record(session, JOB_NAME, day)

    log.info(
        "Daily tick for %s: upcoming=%s (%d DMs for %s), celebration=%s",
        day,
        upcoming_result.outcome,
        upcoming_result.queued,
        upcoming_result.target,
        outcome,
    )
    return TickResult(day=day, upcoming=upcoming_result, celebration=outcome)


def catch_up(session: Session) -> list[TickResult]:
    """Run the tick for every day missed since the last recorded run.

    Called on startup. A bot that was down over someone's birthday still posts,
    late, rather than skipping the day entirely.
    """
    today = clock.today_in(settings_repo.get(session, "timezone"))
    last_run = job_runs_repo.last_run_on(session, JOB_NAME)

    if last_run is None:
        # First ever start: handle today only. Replaying a week of history on a
        # fresh install would fire messages about birthdays that already passed
        # unnoticed.
        return [run(session, today, today=today)]

    missed = _missed_days(last_run, today)
    if not missed:
        return []

    log.info("Catching up on %d missed day(s) since %s", len(missed), last_run)
    return [run(session, day, today=today) for day in missed]


def _missed_days(last_run: date, today: date) -> list[date]:
    """Days after ``last_run`` through ``today``, newest window only."""
    first = max(
        last_run + timedelta(days=1),
        today - timedelta(days=MAX_CATCHUP_DAYS - 1),
    )
    return [
        first + timedelta(days=offset)
        for offset in range((today - first).days + 1)
    ]
