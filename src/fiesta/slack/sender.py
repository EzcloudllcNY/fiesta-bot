"""Draining the outbox into Slack.

Every scheduled message goes out from here. Keeping it to one place is what
makes pacing, retries and error classification a single decision rather than
something every caller has to remember. The only other sends are the instant
replies to a member's own click, which go straight back from the handlers.
"""

import json
import logging
import time
from typing import Any, Protocol

from slack_sdk.errors import SlackApiError
from sqlmodel import Session

from fiesta.locks import drain_lock
from fiesta.models import OutboxMessage, OutboxStatus
from fiesta.repo import gifs as gifs_repo
from fiesta.repo import outbox as outbox_repo

log = logging.getLogger(__name__)

SECONDS_BETWEEN_SENDS = 1.0
"""Slack allows roughly one message per second per channel. The fan-out for one
birthday is about thirty DMs, so this spreads them over half a minute rather
than tripping the rate limiter and having to unwind."""

DEFAULT_RETRY_AFTER_SECONDS = 30
MAX_RETRY_AFTER_SECONDS = 300
"""Slack's own rate limits are measured in seconds, so anything much larger is
either a mistake or a tier limit the next pass can absorb just as well. The
sleep happens on the scheduler thread while the drain lock is held, so an
uncapped Retry-After would park both the queue and any `fiesta send-due` for as
long as the header asked for."""

PERMANENT_ERRORS = frozenset(
    {
        "channel_not_found",  # channel deleted, or the id is wrong
        "user_not_found",  # account fully removed from the workspace
        "account_inactive",  # deactivated user
        "is_bot",  # tried to DM a bot
        "cannot_dm_bot",
    }
)
"""Errors that will fail identically forever. Retrying these five times only
delays every other message in the queue."""


class SlackPoster(Protocol):
    """The slice of the Slack client the sender uses, so tests can stub it."""

    def chat_postMessage(self, **kwargs: Any) -> Any: ...


def drain(
    session: Session,
    client: SlackPoster,
    *,
    limit: int = 50,
    pace: bool = True,
) -> int:
    """Send every message that is due, up to ``limit``.

    Returns how many went out.

    Each message is committed on its own. A crash halfway through a fan-out
    therefore loses nothing and re-sends nothing: whatever was marked sent stays
    sent, and the rest are still pending for the next pass.

    A row is read, posted and only then marked sent, so two drainers overlapping
    would both post it. The lock is taken here rather than at the call sites so
    there is no way to reach the send path around it.
    """
    with drain_lock() as acquired:
        if not acquired:
            log.info(
                "Another process is draining the outbox; "
                "leaving the queue to it"
            )
            return 0
        return _send_due(session, client, limit=limit, pace=pace)


def _send_due(
    session: Session, client: SlackPoster, *, limit: int, pace: bool
) -> int:
    messages = outbox_repo.due(session, limit=limit)
    if not messages:
        return 0

    sent = 0
    for index, message in enumerate(messages):
        if pace and index > 0:
            time.sleep(SECONDS_BETWEEN_SENDS)

        try:
            response = client.chat_postMessage(
                channel=message.channel,
                blocks=message.blocks,
                text=message.fallback_text,
            )
        except SlackApiError as exc:
            if _handle_api_error(session, message, exc):
                session.commit()
                break  # rate limited: stop the pass, the rest stay pending
            session.commit()
            continue
        except Exception as exc:  # network trouble, mostly
            log.warning("Send failed for outbox %s: %s", message.id, exc)
            _record_failed_attempt(session, message, error=str(exc))
            session.commit()
            continue

        outbox_repo.mark_sent(session, message, slack_ts=response.get("ts"))
        session.commit()
        sent += 1

    log.info("Sent %d/%d due messages", sent, len(messages))
    return sent


def _handle_api_error(session: Session, message, exc: SlackApiError) -> bool:
    """Classify a Slack error. Returns True if the whole pass should stop.

    Rate limiting is the one error that says something about the queue rather
    than the message: the message is fine, so it is left pending and the pass
    ends.
    """
    error_code = (
        exc.response.get("error", "unknown") if exc.response else "unknown"
    )

    if (
        error_code == "ratelimited"
        or getattr(exc.response, "status_code", None) == 429
    ):
        retry_after = _retry_after_seconds(exc.response)
        log.warning("Rate limited by Slack; pausing for %ds", retry_after)
        time.sleep(retry_after)
        return True

    if error_code == "invalid_blocks" and _drop_gif(session, message):
        return False

    if error_code in PERMANENT_ERRORS:
        log.warning("Giving up on outbox %s: %s", message.id, error_code)
        outbox_repo.mark_skipped(session, message, reason=error_code)
        return False

    log.warning(
        "Retryable Slack error on outbox %s: %s", message.id, error_code
    )
    _record_failed_attempt(session, message, error=error_code)
    return False


def _drop_gif(session: Session, message: OutboxMessage) -> bool:
    """Count a GIF Slack could not fetch, and send the post without it.

    Slack rejects the whole message with ``invalid_blocks`` when an image URL
    is broken. The text is still fine, so the GIF is struck off and the message
    stays pending for the next pass rather than being lost with it. Returns
    whether there was a GIF to drop.
    """
    blocks = message.blocks
    images = [b for b in blocks if b.get("type") == "image"]
    if not images:
        return False

    for image in images:
        gifs_repo.record_failure(session, image["image_url"])
    message.blocks_json = json.dumps(
        [b for b in blocks if b.get("type") != "image"]
    )
    session.add(message)
    log.warning("Slack could not render the GIF on outbox %s", message.id)
    return True


def _record_failed_attempt(session: Session, message, *, error: str) -> None:
    """Count a transient failure, and shout once the retries run out.

    Logged at ``ERROR`` because it is the one send failure nobody would
    otherwise notice: the message is dropped for good, and that is what Sentry
    picks up.
    """
    outbox_repo.mark_failed_attempt(session, message, error=error)
    if message.status == OutboxStatus.FAILED:
        log.error(
            "Out of retries, outbox %s will not be sent: %s",
            message.id,
            error,
        )


def _retry_after_seconds(response) -> int:
    """How long Slack asked us to wait, clamped to something a drain can afford.

    The header is whatever arrives on the wire, so a missing, absent or
    unparseable one falls back to the default rather than raising in the middle
    of a send.
    """
    try:
        requested = int(
            response.headers.get("Retry-After", DEFAULT_RETRY_AFTER_SECONDS)
        )
    except AttributeError, TypeError, ValueError:
        requested = DEFAULT_RETRY_AFTER_SECONDS

    return max(1, min(requested, MAX_RETRY_AFTER_SECONDS))
