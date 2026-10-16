"""Delivering queued messages, and coping when Slack says no."""

import logging
from typing import ClassVar

from slack_sdk.errors import SlackApiError

from fiesta.models import MessageKind, OutboxStatus
from fiesta.repo import gifs as gifs_repo
from fiesta.repo import outbox as outbox_repo
from fiesta.slack import sender


def queue(session, *, key: str, channel: str = "U_ANA") -> None:
    outbox_repo.enqueue(
        session,
        kind=MessageKind.ASK,
        channel=channel,
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}],
        fallback_text="hi",
        dedupe_key=key,
    )
    session.commit()


def slack_error(
    code: str, *, status: int = 200, retry_after: str | None = None
) -> SlackApiError:
    """A SlackApiError shaped the way the real client raises them."""

    class Response(dict):
        status_code = status
        headers: ClassVar[dict[str, str]] = (
            {} if retry_after is None else {"Retry-After": retry_after}
        )

    return SlackApiError(
        message=code, response=Response({"ok": False, "error": code})
    )


def drain(session, client):
    return sender.drain(session, client, pace=False)


class TestSending:
    def test_a_queued_message_is_delivered(self, session, slack):
        queue(session, key="ask:U_ANA:1")

        sent = drain(session, slack)

        assert sent == 1
        assert slack.channels_messaged == ["U_ANA"]

    def test_the_message_carries_both_blocks_and_fallback_text(
        self, session, slack
    ):
        """The fallback is what shows in the notification and on old clients."""
        queue(session, key="ask:U_ANA:1")

        drain(session, slack)

        (message,) = slack.sent
        assert message.text == "hi"
        assert message.blocks[0]["type"] == "section"

    def test_a_delivered_message_is_not_sent_again(self, session, slack):
        queue(session, key="ask:U_ANA:1")

        drain(session, slack)
        drain(session, slack)

        assert len(slack.sent) == 1

    def test_draining_an_empty_queue_does_nothing(self, session, slack):
        assert drain(session, slack) == 0

    def test_every_due_message_goes_out_in_one_pass(self, session, slack):
        for index in range(5):
            queue(session, key=f"ask:U_{index}:1", channel=f"U_{index}")

        assert drain(session, slack) == 5


class TestPermanentFailures:
    """Some errors will fail identically forever.

    Retrying them only delays the queue.
    """

    def test_a_missing_channel_is_given_up_on_immediately(self, session, slack):
        queue(session, key="celebration:2026-03-10", channel="C_DELETED")
        slack.fail_with = slack_error("channel_not_found")
        slack.fail_times = -1  # fail every time

        drain(session, slack)

        message = outbox_repo.by_dedupe_key(session, "celebration:2026-03-10")
        assert message.status == OutboxStatus.SKIPPED
        assert message.attempts == 0  # no retries were spent

    def test_a_deactivated_user_is_given_up_on_immediately(
        self, session, slack
    ):
        queue(session, key="ask:U_GONE:1", channel="U_GONE")
        slack.fail_with = slack_error("account_inactive")
        slack.fail_times = -1

        drain(session, slack)

        assert outbox_repo.by_dedupe_key(session, "ask:U_GONE:1").status == (
            OutboxStatus.SKIPPED
        )

    def test_one_dead_recipient_does_not_stop_the_rest(self, session, slack):
        """A whole fan-out must not be lost to one deactivated account."""
        queue(session, key="ask:U_GONE:1", channel="U_GONE")
        queue(session, key="ask:U_ANA:1", channel="U_ANA")
        slack.fail_with = slack_error("user_not_found")
        slack.fail_times = 1  # only the first send fails

        sent = drain(session, slack)

        assert sent == 1
        assert slack.channels_messaged == ["U_ANA"]


class TestBrokenGifs:
    """Slack refuses a whole post when it cannot fetch its image."""

    URL = "https://example.test/broken.gif"

    def queue_with_gif(self, session) -> None:
        gifs_repo.add(session, url=self.URL)
        outbox_repo.enqueue(
            session,
            kind=MessageKind.CELEBRATION,
            channel="C_PARTY",
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": "hi"}},
                {"type": "image", "image_url": self.URL, "alt_text": "gif"},
            ],
            fallback_text="Happy birthday!",
            dedupe_key="celebration:2026-03-10:U_ANA",
        )
        session.commit()

    def test_the_post_goes_out_without_the_gif(self, session, slack):
        self.queue_with_gif(session)
        slack.fail_with = slack_error("invalid_blocks")
        slack.fail_times = 1

        drain(session, slack)
        drain(session, slack)

        (message,) = slack.sent
        assert [b["type"] for b in message.blocks] == ["section"]

    def test_the_gif_is_charged_with_a_failure(self, session, slack):
        self.queue_with_gif(session)
        slack.fail_with = slack_error("invalid_blocks")
        slack.fail_times = 1

        drain(session, slack)

        assert gifs_repo.by_url(session, self.URL).fail_count == 1

    def test_dropping_the_gif_does_not_spend_a_retry(self, session, slack):
        self.queue_with_gif(session)
        slack.fail_with = slack_error("invalid_blocks")
        slack.fail_times = 1

        drain(session, slack)

        message = outbox_repo.by_dedupe_key(
            session, "celebration:2026-03-10:U_ANA"
        )
        assert message.status == OutboxStatus.PENDING
        assert message.attempts == 0

    def test_invalid_blocks_without_a_gif_is_an_ordinary_failure(
        self, session, slack
    ):
        queue(session, key="ask:U_ANA:1")
        slack.fail_with = slack_error("invalid_blocks")
        slack.fail_times = 1

        drain(session, slack)

        assert outbox_repo.by_dedupe_key(session, "ask:U_ANA:1").attempts == 1


class TestTransientFailures:
    def test_an_unknown_error_is_retried_later(self, session, slack):
        queue(session, key="ask:U_ANA:1")
        slack.fail_with = slack_error("internal_error")
        slack.fail_times = 1

        drain(session, slack)

        message = outbox_repo.by_dedupe_key(session, "ask:U_ANA:1")
        assert message.status == OutboxStatus.PENDING
        assert message.attempts == 1

    def test_a_retried_message_goes_out_on_a_later_pass(
        self, session, slack, frozen_clock
    ):
        queue(session, key="ask:U_ANA:1")
        slack.fail_with = slack_error("internal_error")
        slack.fail_times = 1

        drain(session, slack)
        frozen_clock.advance(minutes=10)
        sent = drain(session, slack)

        assert sent == 1

    def test_a_network_error_is_treated_as_transient(self, session, slack):
        queue(session, key="ask:U_ANA:1")
        slack.fail_with = ConnectionError("connection reset")
        slack.fail_times = 1

        drain(session, slack)

        message = outbox_repo.by_dedupe_key(session, "ask:U_ANA:1")
        assert message.status == OutboxStatus.PENDING
        assert "connection reset" in message.last_error

    def test_running_out_of_retries_is_logged_as_an_error(
        self, session, slack, caplog
    ):
        """ERROR is what reaches Sentry; a silently dropped message must."""
        queue(session, key="ask:U_ANA:1")
        message = outbox_repo.by_dedupe_key(session, "ask:U_ANA:1")
        message.attempts = outbox_repo.MAX_ATTEMPTS - 1
        session.commit()
        slack.fail_with = slack_error("internal_error")
        slack.fail_times = 1

        drain(session, slack)

        assert message.status == OutboxStatus.FAILED
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "internal_error" in errors[0].getMessage()

    def test_a_retry_left_over_is_not_an_error(self, session, slack, caplog):
        queue(session, key="ask:U_ANA:1")
        slack.fail_with = slack_error("internal_error")
        slack.fail_times = 1

        drain(session, slack)

        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


class TestRateLimiting:
    def test_being_rate_limited_stops_the_pass_without_losing_anything(
        self, session, slack, monkeypatch
    ):
        """The message is fine; the queue is the problem. No penalty."""
        monkeypatch.setattr(sender.time, "sleep", lambda _: None)
        for index in range(3):
            queue(session, key=f"ask:U_{index}:1", channel=f"U_{index}")
        slack.fail_with = slack_error("ratelimited", status=429)
        slack.fail_times = -1

        sent = drain(session, slack)

        assert sent == 0
        still_pending = [m.status for m in outbox_repo.due(session)]
        assert still_pending == [OutboxStatus.PENDING] * 3

    def test_a_rate_limited_message_keeps_all_its_retries(
        self, session, slack, monkeypatch
    ):
        monkeypatch.setattr(sender.time, "sleep", lambda _: None)
        queue(session, key="ask:U_ANA:1")
        slack.fail_with = slack_error("ratelimited", status=429)
        slack.fail_times = -1

        drain(session, slack)

        assert outbox_repo.by_dedupe_key(session, "ask:U_ANA:1").attempts == 0

    def test_slack_decides_how_long_to_wait(self, session, slack, monkeypatch):
        slept = []
        monkeypatch.setattr(sender.time, "sleep", slept.append)
        queue(session, key="ask:U_ANA:1")
        slack.fail_with = slack_error(
            "ratelimited", status=429, retry_after="45"
        )
        slack.fail_times = -1

        drain(session, slack)

        assert slept == [45]

    def test_an_absurd_retry_after_is_capped(self, session, slack, monkeypatch):
        """The sleep holds the drain lock; an hour of it stalls the queue."""
        slept = []
        monkeypatch.setattr(sender.time, "sleep", slept.append)
        queue(session, key="ask:U_ANA:1")
        slack.fail_with = slack_error(
            "ratelimited", status=429, retry_after="3600"
        )
        slack.fail_times = -1

        drain(session, slack)

        assert slept == [sender.MAX_RETRY_AFTER_SECONDS]

    def test_a_nonsense_retry_after_falls_back_instead_of_raising(
        self, session, slack, monkeypatch
    ):
        slept = []
        monkeypatch.setattr(sender.time, "sleep", slept.append)
        queue(session, key="ask:U_ANA:1")
        slack.fail_with = slack_error(
            "ratelimited", status=429, retry_after="soon"
        )
        slack.fail_times = -1

        drain(session, slack)

        assert slept == [sender.DEFAULT_RETRY_AFTER_SECONDS]
