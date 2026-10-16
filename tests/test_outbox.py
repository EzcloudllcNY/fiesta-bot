"""The outbound queue: deduplication, ordering and backoff."""

from datetime import timedelta

from fiesta.models import MessageKind, OutboxStatus
from fiesta.repo import outbox as outbox_repo

BLOCKS = [{"type": "section", "text": {"type": "mrkdwn", "text": "hello"}}]


def enqueue(
    session, *, key: str, channel: str = "U_ANA", scheduled_for=None
) -> bool:
    return outbox_repo.enqueue(
        session,
        kind=MessageKind.ASK,
        channel=channel,
        blocks=BLOCKS,
        fallback_text="hello",
        dedupe_key=key,
        scheduled_for=scheduled_for,
    )


class TestDeduplication:
    """The unique dedupe key is what makes every job safe to re-run."""

    def test_the_same_key_is_only_queued_once(self, session):
        assert enqueue(session, key="ask:U_ANA:1") is True
        assert enqueue(session, key="ask:U_ANA:1") is False
        assert len(outbox_repo.due(session)) == 1

    def test_different_keys_are_queued_separately(self, session):
        enqueue(session, key="ask:U_ANA:1")
        enqueue(session, key="ask:U_ANA:2")

        assert len(outbox_repo.due(session)) == 2

    def test_a_duplicate_does_not_disturb_the_original(self, session):
        enqueue(session, key="ask:U_ANA:1", channel="U_ANA")
        enqueue(session, key="ask:U_ANA:1", channel="U_SOMEONE_ELSE")

        (message,) = outbox_repo.due(session)
        assert message.channel == "U_ANA"

    def test_a_key_stays_claimed_after_the_message_is_sent(self, session):
        """Otherwise a catch-up replay would re-send everything already sent."""
        enqueue(session, key="celebration:2026-03-10")
        (message,) = outbox_repo.due(session)
        outbox_repo.mark_sent(session, message, slack_ts="1.0")
        session.commit()

        assert enqueue(session, key="celebration:2026-03-10") is False


class TestWhatIsDue:
    def test_a_message_scheduled_for_later_is_not_due_yet(
        self, session, frozen_clock
    ):
        enqueue(
            session,
            key="later",
            scheduled_for=frozen_clock.now + timedelta(hours=1),
        )

        assert outbox_repo.due(session) == []

        frozen_clock.advance(hours=1)
        assert len(outbox_repo.due(session)) == 1

    def test_the_oldest_message_comes_first(self, session, frozen_clock):
        enqueue(
            session,
            key="second",
            scheduled_for=frozen_clock.now + timedelta(minutes=5),
        )
        enqueue(session, key="first", scheduled_for=frozen_clock.now)

        frozen_clock.advance(hours=1)

        assert [m.dedupe_key for m in outbox_repo.due(session)] == [
            "first",
            "second",
        ]

    def test_only_a_batch_is_returned_at_a_time(self, session):
        for index in range(10):
            enqueue(session, key=f"message-{index}")

        assert len(outbox_repo.due(session, limit=4)) == 4

    def test_sent_messages_are_not_returned_again(self, session):
        enqueue(session, key="done")
        (message,) = outbox_repo.due(session)

        outbox_repo.mark_sent(session, message, slack_ts="1.0")
        session.commit()

        assert outbox_repo.due(session) == []


class TestFailureHandling:
    def test_a_failed_attempt_is_retried_later(self, session, frozen_clock):
        enqueue(session, key="flaky")
        (message,) = outbox_repo.due(session)

        outbox_repo.mark_failed_attempt(session, message, error="timeout")
        session.commit()

        assert outbox_repo.due(session) == []  # backed off
        frozen_clock.advance(minutes=5)
        assert len(outbox_repo.due(session)) == 1

    def test_each_retry_waits_longer_than_the_last(self, session, frozen_clock):
        enqueue(session, key="flaky")
        (message,) = outbox_repo.due(session)

        waits = []
        for _ in range(3):
            before = message.scheduled_for
            outbox_repo.mark_failed_attempt(session, message, error="timeout")
            waits.append(message.scheduled_for - before)

        assert waits == sorted(waits)
        assert waits[0] < waits[-1]

    def test_a_message_is_given_up_on_after_enough_attempts(
        self, session, frozen_clock
    ):
        enqueue(session, key="doomed")
        (message,) = outbox_repo.due(session)

        for _ in range(outbox_repo.MAX_ATTEMPTS):
            outbox_repo.mark_failed_attempt(session, message, error="timeout")
        session.commit()

        assert message.status == OutboxStatus.FAILED
        frozen_clock.advance(days=7)
        assert outbox_repo.due(session) == []

    def test_a_skipped_message_is_never_retried(self, session, frozen_clock):
        """Some errors fail identically forever.

        Retrying them just delays the queue.
        """
        enqueue(session, key="bad-channel")
        (message,) = outbox_repo.due(session)

        outbox_repo.mark_skipped(session, message, reason="channel_not_found")
        session.commit()

        frozen_clock.advance(days=7)
        assert outbox_repo.due(session) == []
        assert message.status == OutboxStatus.SKIPPED


class TestPayload:
    def test_blocks_survive_the_round_trip(self, session):
        enqueue(session, key="round-trip")

        (message,) = outbox_repo.due(session)
        assert message.blocks == BLOCKS
