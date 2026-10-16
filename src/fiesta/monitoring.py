"""Error reporting to Sentry.

Nothing here reports errors by hand. The SDK's default integrations already
cover every way this app fails: an uncaught exception in a command, and any
``ERROR`` log record — which is how APScheduler reports a job that raised and
how Bolt reports a listener that raised. ``INFO`` and ``WARNING`` records ride
along as breadcrumbs on the next event.

The one exception is Socket Mode's own chatter about a dropped connection,
which :func:`_drop_socket_noise` filters out.
"""

import logging
import subprocess

import sentry_sdk

from fiesta.config import Config


def init_sentry(config: Config) -> bool:
    """Start reporting, if a DSN is configured. Returns whether it did."""
    if not config.sentry_dsn:
        return False

    sentry_sdk.init(
        dsn=config.sentry_dsn,
        environment=config.sentry_environment,
        release=config.sentry_release or _git_short_sha(),
        # Slack user ids and birthdays stay out of Sentry.
        send_default_pii=False,
        before_send=_drop_socket_noise,
    )
    return True


# Logged at ERROR by slack_sdk's builtin Socket Mode client each time its
# periodic ping hits a socket Slack or the network has already closed (a
# BrokenPipeError, then SSL errors). After a few in a row it drops the session
# and reconnects by itself, so nothing is lost. A reconnect that actually
# fails logs a different message and still gets reported.
_SOCKET_NOISE = "Failed to check the state of sock"


def _drop_socket_noise(event, hint):
    record = hint.get("log_record")
    if isinstance(record, logging.LogRecord) and record.getMessage().startswith(
        _SOCKET_NOISE
    ):
        return None
    return event


def _git_short_sha() -> str | None:
    """The checked-out commit, for runs straight from the repo.

    The Docker image has no ``.git``; its build bakes the sha into
    ``SENTRY_RELEASE`` instead. ``None`` leaves the release unset.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except OSError, subprocess.CalledProcessError:
        return None
    return result.stdout.strip() or None
