"""The curated GIF pool. Phase 2, but the selection rules are settled."""

from fiesta.models import MAX_FAILURES
from fiesta.repo import gifs as gifs_repo


def add_gifs(session, count: int) -> None:
    for index in range(count):
        gifs_repo.add(session, url=f"https://example.test/{index}.gif")
    session.commit()


class TestPicking:
    def test_an_empty_pool_yields_nothing(self, session):
        assert gifs_repo.pick(session) is None

    def test_a_gif_is_returned_when_one_exists(self, session):
        add_gifs(session, 1)

        assert gifs_repo.pick(session).url == "https://example.test/0.gif"

    def test_picking_records_that_it_was_used(self, session, frozen_clock):
        add_gifs(session, 1)

        chosen = gifs_repo.pick(session)

        assert chosen.last_used_at == frozen_clock.now

    def test_the_same_gif_does_not_come_up_twice_running(self, session):
        """A repeat two birthdays in a row is what people actually notice."""
        add_gifs(session, 2)
        first = gifs_repo.pick(session)
        session.commit()

        second = gifs_repo.pick(session)

        assert second.url != first.url

    def test_a_large_pool_still_rotates_rather_than_repeating(self, session):
        """With many GIFs the pick is random, but only among the stalest."""
        add_gifs(session, 40)

        picked = []
        for _ in range(10):
            picked.append(gifs_repo.pick(session).url)
            session.commit()

        assert len(set(picked)) == 10


class TestRetiringBrokenGifs:
    def test_a_disabled_gif_is_never_picked(self, session):
        add_gifs(session, 1)
        gif = gifs_repo.pick(session)
        gif.enabled = False
        session.commit()

        assert gifs_repo.pick(session) is None

    def test_a_gif_that_keeps_failing_drops_out_of_the_rotation(self, session):
        add_gifs(session, 1)
        url = "https://example.test/0.gif"

        for _ in range(MAX_FAILURES):
            gifs_repo.record_failure(session, url)
        session.commit()

        assert gifs_repo.pick(session) is None

    def test_one_failure_is_forgiven(self, session):
        """A single hiccup shouldn't cost a good GIF its place."""
        add_gifs(session, 1)
        gifs_repo.record_failure(session, "https://example.test/0.gif")
        session.commit()

        assert gifs_repo.pick(session) is not None


class TestEditing:
    def test_a_new_url_starts_with_a_clean_failure_count(self, session):
        """The failures were the old URL's, not the new one's."""
        add_gifs(session, 1)
        (gif,) = gifs_repo.all_gifs(session)
        gif.fail_count = MAX_FAILURES

        gifs_repo.update(session, gif, url="https://example.test/new.gif")

        assert gif.fail_count == 0

    def test_retagging_keeps_the_failure_count(self, session):
        add_gifs(session, 1)
        (gif,) = gifs_repo.all_gifs(session)
        gif.fail_count = 2

        gifs_repo.update(session, gif, tags="cake")

        assert gif.fail_count == 2
