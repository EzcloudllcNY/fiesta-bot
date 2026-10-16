"""When each scheduled job last completed."""

from datetime import date

from sqlmodel import Field, SQLModel


class JobRun(SQLModel, table=True):
    """The last local date a job finished for.

    A date rather than a timestamp because that is the question catch-up asks:
    which calendar days did the bot miss while it was down?
    """

    __tablename__ = "job_run"

    name: str = Field(primary_key=True)
    last_run_on: date
