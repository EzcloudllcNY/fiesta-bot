"""The background jobs, and what runs when the bot starts.

APScheduler is kept in-process and in-memory. It doesn't need a persistent job
store because the outbox already provides durability: a job that never ran
leaves no rows, and the startup catch-up replays the days it missed.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from fiesta import clock
from fiesta.db import session_scope
from fiesta.repo import settings as settings_repo
from fiesta.services import daily, roster
from fiesta.slack import sender

log = logging.getLogger(__name__)

DRAIN_INTERVAL_SECONDS = 30
ASK_INTERVAL_MINUTES = 15
ROSTER_SYNC_HOUR = 3


def build_scheduler(client) -> BackgroundScheduler:
    """Wire up the four recurring jobs.

    ``post_time`` and ``timezone`` are read once here, at startup. Changing
    either takes a restart — a deliberate trade, since re-reading them on every
    tick would mean rescheduling the cron trigger from inside its own job.
    """
    with session_scope() as session:
        post_time = settings_repo.get(session, "post_time")
        tz_name = settings_repo.get(session, "timezone")

    hour, minute = (int(part) for part in post_time.split(":"))
    scheduler = BackgroundScheduler(timezone=tz_name)

    scheduler.add_job(
        lambda: _daily_tick(tz_name),
        CronTrigger(hour=hour, minute=minute, timezone=tz_name),
        id="daily_tick",
        # If the bot was asleep at post_time, run as soon as it wakes rather
        # than waiting a full day.
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        lambda: _drain(client),
        IntervalTrigger(seconds=DRAIN_INTERVAL_SECONDS),
        id="drain_outbox",
        max_instances=1,  # a slow, paced drain must never overlap itself
    )
    scheduler.add_job(
        _process_asks,
        IntervalTrigger(minutes=ASK_INTERVAL_MINUTES),
        id="process_asks",
    )
    scheduler.add_job(
        lambda: _sync_roster(client),
        CronTrigger(hour=ROSTER_SYNC_HOUR, minute=0, timezone=tz_name),
        id="sync_roster",
    )

    log.info("Scheduler ready: daily tick at %s %s", post_time, tz_name)
    return scheduler


def run_startup_tasks(client) -> None:
    """Catch up on anything missed while the bot was down.

    Roster sync goes first so that catch-up sees anyone who joined during the
    outage.
    """
    _sync_roster(client)

    with session_scope() as session:
        results = daily.catch_up(session)

    if results:
        log.info("Startup catch-up handled %d day(s)", len(results))


def _daily_tick(tz_name: str) -> None:
    with session_scope() as session:
        daily.run(session, clock.today_in(tz_name))


def _drain(client) -> None:
    with session_scope() as session:
        # Checked here rather than in the sender, so `fiesta send-due` still
        # delivers by hand while the automatic loop is paused. The queue keeps
        # filling either way.
        if not settings_repo.get(session, "auto_send_enabled"):
            return
        sender.drain(session, client)


def _process_asks() -> None:
    from fiesta.services import onboarding

    with session_scope() as session:
        onboarding.enqueue_due_asks(session)


def _sync_roster(client) -> None:
    with session_scope() as session:
        roster.sync(session, client)
