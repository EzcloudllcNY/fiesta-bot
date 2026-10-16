"""Making sure only one process at a time drains the outbox.

``dedupe_key`` stops a message being *queued* twice; nothing about it stops two
processes *sending* the same queued row. That is not a rare race: ``fiesta
send-due`` is documented as the recovery tool, so reaching for it while the bot
is running is the normal way an operator would hit it, and the result is the
whole team getting the birthday post twice.

A file lock is enough because the invariant is simply "one drainer". The kernel
drops it when the holder exits, so a crashed bot leaves nothing to clean up.
"""

import fcntl
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fiesta.db import get_engine

log = logging.getLogger(__name__)


def drain_lock_path() -> Path | None:
    """The lock file, beside the database.

    ``None`` for an in-memory database.
    """
    database = get_engine().url.database
    if not database or database == ":memory:":
        return None
    return Path(f"{database}.drain.lock")


@contextmanager
def drain_lock() -> Iterator[bool]:
    """Hold the drain lock for the block, yielding whether it was acquired.

    An in-memory database belongs to this process alone, so there is nobody to
    contend with and the lock is skipped — which is also what keeps it out of
    the way in tests.
    """
    path = drain_lock_path()
    if path is None:
        yield True
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return

        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
