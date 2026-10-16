"""The birthday-day post in the celebration channel."""

from datetime import date

from factories import add_birthday, add_member, add_team, configure
from fiesta.models import MessageKind
from fiesta.repo import gifs as gifs_repo
from fiesta.repo import outbox as outbox_repo
from fiesta.repo import templates as templates_repo
from fiesta.services import celebration
from fiesta.services.celebration import Outcome

TODAY = date(2026, 3, 10)
CHANNEL = "C_GENERAL"


def the_post(session):
    (message,) = outbox_repo.due(session)
    return message


class TestPosting:
    def test_a_birthday_today_is_posted_to_the_channel(self, session):
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (3, 10))
        configure(session, celebration_channel=CHANNEL)

        assert celebration.enqueue_for(session, TODAY) is Outcome.QUEUED
        assert the_post(session).channel == CHANNEL
        assert the_post(session).kind == MessageKind.CELEBRATION

    def test_nothing_is_posted_when_nobody_has_a_birthday(self, session):
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (9, 1))
        configure(session, celebration_channel=CHANNEL)

        assert (
            celebration.enqueue_for(session, TODAY) is Outcome.NOTHING_TO_POST
        )

    def test_nothing_is_posted_when_no_channel_is_configured(self, session):
        """A fresh install shouldn't guess at a channel. It stays quiet."""
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (3, 10))

        assert celebration.enqueue_for(session, TODAY) is Outcome.NOT_CONFIGURED

    def test_a_hidden_birthday_is_not_posted(self, session):
        add_member(session, "U_SHY")
        add_birthday(session, "U_SHY", (3, 10), hidden=True)
        configure(session, celebration_channel=CHANNEL)

        assert (
            celebration.enqueue_for(session, TODAY) is Outcome.NOTHING_TO_POST
        )


class TestSeveralBirthdays:
    """Several birthdays on one day get a post each."""

    def test_two_birthdays_get_two_posts(self, session):
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 10))
        add_birthday(session, "U_BEN", (3, 10))
        configure(session, celebration_channel=CHANNEL)

        celebration.enqueue_for(session, TODAY)

        assert len(outbox_repo.due(session)) == 2

    def test_each_post_mentions_only_its_own_person(self, session):
        add_team(session, "U_ANA", "U_BEN", "U_CAL")
        add_birthday(session, "U_ANA", (3, 10))
        add_birthday(session, "U_BEN", (3, 10))
        configure(session, celebration_channel=CHANNEL)
        templates_repo.add(session, text="Happy birthday {user}")
        session.commit()

        celebration.enqueue_for(session, TODAY)

        posts = [str(m.blocks) for m in outbox_repo.due(session)]
        assert ["<@U_ANA>" in p for p in posts] == [True, False]
        assert ["<@U_BEN>" in p for p in posts] == [False, True]
        assert not any("<@U_CAL>" in p for p in posts)

    def test_a_birthday_added_after_the_run_is_still_posted(self, session):
        """A re-run queues whoever is new and nobody twice."""
        add_team(session, "U_ANA", "U_BEN")
        add_birthday(session, "U_ANA", (3, 10))
        configure(session, celebration_channel=CHANNEL)
        celebration.enqueue_for(session, TODAY)

        add_birthday(session, "U_BEN", (3, 10))

        assert celebration.enqueue_for(session, TODAY) is Outcome.QUEUED
        assert len(outbox_repo.due(session)) == 2


class TestRunningTwice:
    def test_running_the_job_twice_posts_once(self, session):
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (3, 10))
        configure(session, celebration_channel=CHANNEL)

        first = celebration.enqueue_for(session, TODAY)
        second = celebration.enqueue_for(session, TODAY)

        assert (first, second) == (Outcome.QUEUED, Outcome.ALREADY_QUEUED)
        assert len(outbox_repo.due(session)) == 1

    def test_the_same_birthday_is_posted_again_next_year(self, session):
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (3, 10))
        configure(session, celebration_channel=CHANNEL)

        celebration.enqueue_for(session, TODAY)

        assert (
            celebration.enqueue_for(session, date(2027, 3, 10))
            is Outcome.QUEUED
        )


class TestGifs:
    """Phase 2.

    The wiring exists now; the pool is empty until it is switched on.
    """

    def test_no_gif_is_attached_by_default(self, session):
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (3, 10))
        configure(session, celebration_channel=CHANNEL)
        gifs_repo.add(session, url="https://example.test/party.gif")
        session.commit()

        celebration.enqueue_for(session, TODAY)

        block_types = [block["type"] for block in the_post(session).blocks]
        assert "image" not in block_types

    def test_a_gif_is_attached_once_the_provider_is_switched_on(self, session):
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (3, 10))
        configure(session, celebration_channel=CHANNEL, gif_provider="db")
        gifs_repo.add(session, url="https://example.test/party.gif")
        session.commit()

        celebration.enqueue_for(session, TODAY)

        images = [b for b in the_post(session).blocks if b["type"] == "image"]
        assert [b["image_url"] for b in images] == [
            "https://example.test/party.gif"
        ]

    def test_the_text_still_posts_when_the_gif_pool_is_empty(self, session):
        """Requirement: a missing GIF must never cost the team its message."""
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (3, 10))
        configure(session, celebration_channel=CHANNEL, gif_provider="db")

        assert celebration.enqueue_for(session, TODAY) is Outcome.QUEUED
        assert "<@U_ANA>" in str(the_post(session).blocks)


class TestSwitchedOff:
    def test_nothing_is_posted_when_celebrations_are_off(self, session):
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (3, 10))
        configure(
            session, celebration_channel=CHANNEL, celebration_enabled=False
        )

        assert celebration.enqueue_for(session, TODAY) is Outcome.DISABLED
        assert outbox_repo.due(session) == []
