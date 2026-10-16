"""Asking new members for their birthday, and knowing when to stop."""

from factories import add_birthday, add_member, add_team, configure
from fiesta.models import AskStatus, MessageKind
from fiesta.repo import asks as asks_repo
from fiesta.repo import outbox as outbox_repo
from fiesta.services import onboarding


def queue_member(session, user_id: str, *, delay_days: int = 3):
    """A member who has just joined and is waiting out the pre-ask delay."""
    add_member(session, user_id)
    state = asks_repo.create_pending(session, user_id, delay_days=delay_days)
    session.commit()
    return state


def pending_messages(session):
    return outbox_repo.due(session)


class TestTheFirstAsk:
    def test_nobody_is_asked_before_the_delay_is_up(
        self, session, frozen_clock
    ):
        queue_member(session, "U_NEW", delay_days=3)

        frozen_clock.advance(days=2)
        queued = onboarding.enqueue_due_asks(session)

        assert queued == 0
        assert pending_messages(session) == []

    def test_the_ask_goes_out_once_the_delay_has_passed(
        self, session, frozen_clock
    ):
        queue_member(session, "U_NEW", delay_days=3)

        frozen_clock.advance(days=3)
        queued = onboarding.enqueue_due_asks(session)

        assert queued == 1
        (message,) = pending_messages(session)
        assert message.channel == "U_NEW"
        assert message.kind == MessageKind.ASK

    def test_asking_moves_the_member_out_of_pending(
        self, session, frozen_clock
    ):
        queue_member(session, "U_NEW", delay_days=0)

        onboarding.enqueue_due_asks(session)

        state = asks_repo.get(session, "U_NEW")
        assert state.status == AskStatus.ASKED
        assert state.asks_sent == 1


class TestReminders:
    def test_a_reminder_waits_for_the_interval(self, session, frozen_clock):
        configure(session, max_asks=3, ask_interval_days=4)
        queue_member(session, "U_QUIET", delay_days=0)
        onboarding.enqueue_due_asks(session)

        frozen_clock.advance(days=3)
        assert onboarding.enqueue_due_asks(session) == 0

        frozen_clock.advance(days=1)
        assert onboarding.enqueue_due_asks(session) == 1

    def test_the_bot_gives_up_after_max_asks(self, session, frozen_clock):
        configure(session, max_asks=3, ask_interval_days=4)
        queue_member(session, "U_QUIET", delay_days=0)

        for _ in range(5):  # more rounds than max_asks, to prove it stops
            onboarding.enqueue_due_asks(session)
            frozen_clock.advance(days=4)

        state = asks_repo.get(session, "U_QUIET")
        assert state.asks_sent == 3
        assert state.status == AskStatus.EXHAUSTED
        assert len(pending_messages(session)) == 3

    def test_an_exhausted_member_is_never_asked_again(
        self, session, frozen_clock
    ):
        configure(session, max_asks=1)
        queue_member(session, "U_QUIET", delay_days=0)
        onboarding.enqueue_due_asks(session)

        frozen_clock.advance(days=365)

        assert onboarding.enqueue_due_asks(session) == 0

    def test_the_first_ask_reads_differently_from_a_reminder(
        self, session, frozen_clock
    ):
        configure(session, max_asks=3, ask_interval_days=1)
        queue_member(session, "U_QUIET", delay_days=0)

        onboarding.enqueue_due_asks(session)
        frozen_clock.advance(days=1)
        onboarding.enqueue_due_asks(session)

        first, reminder = pending_messages(session)
        assert "Welcome aboard" in str(first.blocks)
        assert "Still no birthday" in str(reminder.blocks)

    def test_the_last_ask_says_it_is_the_last(self, session, frozen_clock):
        """Someone who isn't interested should know they can ignore it."""
        configure(session, max_asks=2, ask_interval_days=1)
        queue_member(session, "U_QUIET", delay_days=0)

        onboarding.enqueue_due_asks(session)
        frozen_clock.advance(days=1)
        onboarding.enqueue_due_asks(session)

        first, last = pending_messages(session)
        assert "last time" not in str(first.blocks)
        assert "last time" in str(last.blocks)


class TestStopping:
    def test_answering_stops_the_asks(self, session, frozen_clock):
        configure(session, max_asks=3, ask_interval_days=1)
        queue_member(session, "U_ANA", delay_days=0)
        onboarding.enqueue_due_asks(session)

        asks_repo.mark_answered(session, "U_ANA")
        session.commit()
        frozen_clock.advance(days=30)

        assert onboarding.enqueue_due_asks(session) == 0
        assert asks_repo.get(session, "U_ANA").status == AskStatus.ANSWERED

    def test_never_ask_me_stops_the_asks(self, session, frozen_clock):
        configure(session, max_asks=3, ask_interval_days=1)
        queue_member(session, "U_PRIVATE", delay_days=0)
        onboarding.enqueue_due_asks(session)

        asks_repo.mark_declined(session, "U_PRIVATE")
        session.commit()
        frozen_clock.advance(days=30)

        assert onboarding.enqueue_due_asks(session) == 0

    def test_a_member_can_answer_before_ever_being_asked(self, session):
        """Someone who runs /birthday on day one has no ask_state row yet."""
        add_member(session, "U_EAGER")

        state = asks_repo.mark_answered(session, "U_EAGER")
        session.commit()

        assert state.status == AskStatus.ANSWERED


class TestRunningTwice:
    """The job runs every fifteen minutes and can be replayed on startup."""

    def test_running_the_job_twice_does_not_ask_twice(self, session):
        queue_member(session, "U_NEW", delay_days=0)

        onboarding.enqueue_due_asks(session)
        onboarding.enqueue_due_asks(session)

        assert len(pending_messages(session)) == 1

    def test_a_deduplicated_replay_does_not_burn_an_ask(
        self, session, frozen_clock
    ):
        """Counting a deduplicated enqueue would cost the member a reminder."""
        configure(session, max_asks=3, ask_interval_days=4)
        queue_member(session, "U_NEW", delay_days=0)

        onboarding.enqueue_due_asks(session)
        first_count = asks_repo.get(session, "U_NEW").asks_sent

        # An outbox row for attempt 1 already exists, so this enqueue collides.
        frozen_clock.advance(days=4)
        onboarding.enqueue_due_asks(session)

        messages = pending_messages(session)
        assert first_count == 1
        assert [m.dedupe_key for m in messages] == [
            "ask:U_NEW:1",
            "ask:U_NEW:2",
        ]


class TestTheRollout:
    """`fiesta announce`: the bot is the new arrival, not the people."""

    def test_everyone_without_a_birthday_is_introduced_to(self, session):
        add_team(session, "U_ANA", "U_BEN", "U_CAL")

        queued = onboarding.enqueue_introductions(session)

        assert queued == 3
        assert {m.channel for m in pending_messages(session)} == {
            "U_ANA",
            "U_BEN",
            "U_CAL",
        }

    def test_the_message_says_the_bot_is_new_not_the_member(self, session):
        add_member(session, "U_VETERAN")

        onboarding.enqueue_introductions(session)

        (message,) = pending_messages(session)
        assert "I'm new here" in str(message.blocks)
        assert "Welcome aboard" not in str(message.blocks)

    def test_someone_who_already_gave_a_birthday_is_left_alone(self, session):
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 15))

        onboarding.enqueue_introductions(session)

        assert {m.channel for m in pending_messages(session)} == {"U_BEN"}

    def test_someone_who_asked_not_to_be_asked_is_left_alone(self, session):
        add_team(session, "U_ANA", "U_PRIVATE")
        asks_repo.mark_declined(session, "U_PRIVATE")
        session.commit()

        onboarding.enqueue_introductions(session)

        assert {m.channel for m in pending_messages(session)} == {"U_ANA"}

    def test_bots_and_deactivated_accounts_are_left_alone(self, session):
        add_member(session, "U_ANA")
        add_member(session, "U_ROBOT", is_bot=True)
        add_member(session, "U_GONE", is_deleted=True)

        onboarding.enqueue_introductions(session)

        assert {m.channel for m in pending_messages(session)} == {"U_ANA"}

    def test_announcing_twice_does_not_message_twice(self, session):
        add_team(session, "U_ANA", "U_BEN")

        first = onboarding.enqueue_introductions(session)
        second = onboarding.enqueue_introductions(session)

        assert (first, second) == (2, 0)

    def test_the_introduction_takes_the_place_of_the_first_ask(
        self, session, frozen_clock
    ):
        """Nobody gets the rollout message and then a new-joiner welcome."""
        queue_member(session, "U_ANA", delay_days=3)

        onboarding.enqueue_introductions(session)
        frozen_clock.advance(days=3)
        onboarding.enqueue_due_asks(session)

        assert len(pending_messages(session)) == 1

    def test_the_ordinary_reminders_follow_on_from_it(
        self, session, frozen_clock
    ):
        configure(session, max_asks=3, ask_interval_days=4)
        add_member(session, "U_ANA")

        onboarding.enqueue_introductions(session)
        frozen_clock.advance(days=4)
        onboarding.enqueue_due_asks(session)

        first, second = pending_messages(session)
        assert "I'm new here" in str(first.blocks)
        assert "Still no birthday" in str(second.blocks)

    def test_someone_already_part_way_through_their_reminders_is_left_alone(
        self, session
    ):
        """They are already in the cadence.

        The rollout must not jump them ahead of it.
        """
        queue_member(session, "U_QUIET", delay_days=0)
        onboarding.enqueue_due_asks(session)

        assert onboarding.enqueue_introductions(session) == 0

    def test_a_dry_run_lists_exactly_who_would_be_messaged(self, session):
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 15))

        recipients = onboarding.introduction_recipients(session)

        assert [m.slack_user_id for m in recipients] == ["U_BEN"]
        assert pending_messages(session) == []  # previewing sends nothing


class TestSwitchedOff:
    def test_nobody_is_asked_while_asks_are_off(self, session, frozen_clock):
        queue_member(session, "U_NEW", delay_days=3)
        configure(session, asks_enabled=False)

        frozen_clock.advance(days=3)

        assert onboarding.enqueue_due_asks(session) == 0
        assert pending_messages(session) == []

    def test_switching_asks_back_on_picks_up_where_it_left_off(
        self, session, frozen_clock
    ):
        """Pausing burns no ask; the first one still reads as a welcome."""
        queue_member(session, "U_NEW", delay_days=3)
        configure(session, asks_enabled=False)
        frozen_clock.advance(days=10)
        onboarding.enqueue_due_asks(session)

        configure(session, asks_enabled=True)

        assert onboarding.enqueue_due_asks(session) == 1
        assert asks_repo.get(session, "U_NEW").asks_sent == 1
