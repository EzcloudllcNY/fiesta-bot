"""Recording what each scheduled job last completed."""

from datetime import date

from sqlmodel import Session

from fiesta.models import JobRun


def last_run_on(session: Session, name: str) -> date | None:
    row = session.get(JobRun, name)
    return row.last_run_on if row else None


def record(session: Session, name: str, day: date) -> None:
    """Store ``day`` as the job's newest completed date.

    Never moves backwards, so replaying an older day during catch-up can't make
    the bot think it has more to catch up on next time.
    """
    row = session.get(JobRun, name)
    if row is None:
        session.add(JobRun(name=name, last_run_on=day))
        return

    if row.last_run_on < day:
        row.last_run_on = day
        session.add(row)
