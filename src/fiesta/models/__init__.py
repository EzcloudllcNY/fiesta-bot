"""Every table, re-exported so importing this module registers the full schema.

Alembic's autogenerate and the test fixtures both rely on that: a model that
isn't imported here is invisible to ``SQLModel.metadata``.
"""

from sqlmodel import SQLModel

from fiesta.models.ask import TERMINAL_STATUSES, AskState, AskStatus
from fiesta.models.birthday import Birthday
from fiesta.models.gif import MAX_FAILURES, Gif
from fiesta.models.job_run import JobRun
from fiesta.models.member import Member, MemberPrefs
from fiesta.models.outbox import MessageKind, OutboxMessage, OutboxStatus
from fiesta.models.setting import Setting
from fiesta.models.template import CongratsTemplate

# Names every constraint. SQLite cannot drop an unnamed constraint, so without
# this a future migration that needs to alter one of these tables has nothing to
# refer to.
SQLModel.metadata.naming_convention = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

__all__ = [
    "MAX_FAILURES",
    "TERMINAL_STATUSES",
    "AskState",
    "AskStatus",
    "Birthday",
    "CongratsTemplate",
    "Gif",
    "JobRun",
    "Member",
    "MemberPrefs",
    "MessageKind",
    "OutboxMessage",
    "OutboxStatus",
    "Setting",
]
