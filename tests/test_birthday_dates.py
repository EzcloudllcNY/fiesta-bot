"""Date handling: which stored birthdays count as falling on a given day."""

from datetime import date

import pytest

from factories import add_birthday, add_member
from fiesta.repo import birthdays as birthdays_repo


class TestLeapDay:
    """29 February exists one year in four; the birthday comes every year."""

    def test_leap_day_birthday_is_celebrated_on_the_29th_in_a_leap_year(
        self, session
    ):
        add_member(session, "U_LEAPLING")
        add_birthday(session, "U_LEAPLING", (2, 29))

        celebrants = birthdays_repo.falling_on(session, date(2028, 2, 29))

        assert [b.member_id for b in celebrants] == ["U_LEAPLING"]

    def test_leap_day_birthday_moves_to_the_28th_in_a_non_leap_year(
        self, session
    ):
        add_member(session, "U_LEAPLING")
        add_birthday(session, "U_LEAPLING", (2, 29))

        celebrants = birthdays_repo.falling_on(session, date(2026, 2, 28))

        assert [b.member_id for b in celebrants] == ["U_LEAPLING"]

    def test_the_stored_date_is_never_rewritten(self, session):
        """A lookup shift only; the birthday snaps back next leap year."""
        add_member(session, "U_LEAPLING")
        add_birthday(session, "U_LEAPLING", (2, 29))

        birthdays_repo.falling_on(session, date(2026, 2, 28))

        stored = birthdays_repo.get(session, "U_LEAPLING")
        assert (stored.month, stored.day) == (2, 29)

    def test_the_28th_does_not_absorb_leap_birthdays_in_a_leap_year(
        self, session
    ):
        """In a leap year the 29th is its own day; the 28th must not take it."""
        add_member(session, "U_LEAPLING")
        add_birthday(session, "U_LEAPLING", (2, 29))

        celebrants = birthdays_repo.falling_on(session, date(2028, 2, 28))

        assert celebrants == []

    def test_people_born_on_the_28th_are_unaffected(self, session):
        add_member(session, "U_28TH")
        add_birthday(session, "U_28TH", (2, 28))

        in_leap_year = birthdays_repo.falling_on(session, date(2028, 2, 28))
        in_normal_year = birthdays_repo.falling_on(session, date(2026, 2, 28))

        assert [b.member_id for b in in_leap_year] == ["U_28TH"]
        assert [b.member_id for b in in_normal_year] == ["U_28TH"]


class TestWhoIsIncluded:
    """falling_on filters here so no caller can forget the privacy rules."""

    def test_hidden_birthdays_are_left_out(self, session):
        add_member(session, "U_SHY")
        add_birthday(session, "U_SHY", (6, 1), hidden=True)

        assert birthdays_repo.falling_on(session, date(2026, 6, 1)) == []

    def test_deactivated_members_are_left_out(self, session):
        add_member(session, "U_GONE", is_deleted=True)
        add_birthday(session, "U_GONE", (6, 1))

        assert birthdays_repo.falling_on(session, date(2026, 6, 1)) == []

    def test_bots_are_left_out(self, session):
        add_member(session, "U_ROBOT", is_bot=True)
        add_birthday(session, "U_ROBOT", (6, 1))

        assert birthdays_repo.falling_on(session, date(2026, 6, 1)) == []

    def test_everyone_sharing_a_date_is_returned(self, session):
        add_member(session, "U_ANA")
        add_member(session, "U_BEN")
        add_birthday(session, "U_ANA", (6, 1))
        add_birthday(session, "U_BEN", (6, 1))

        celebrants = birthdays_repo.falling_on(session, date(2026, 6, 1))

        assert {b.member_id for b in celebrants} == {"U_ANA", "U_BEN"}


class TestValidation:
    """Impossible dates are rejected before they reach the database."""

    @pytest.mark.parametrize(
        ("month", "day"),
        [
            (2, 30),  # February never has 30 days
            (4, 31),  # April has 30
            (13, 1),  # no thirteenth month
            (0, 1),
            (1, 0),
        ],
    )
    def test_impossible_dates_are_rejected(self, month, day):
        with pytest.raises(ValueError, match="must be between"):
            birthdays_repo.validate(month, day, None)

    def test_the_29th_of_february_is_accepted(self):
        birthdays_repo.validate(2, 29, None)  # does not raise

    def test_a_missing_year_is_fine(self):
        birthdays_repo.validate(7, 4, None)  # does not raise

    def test_an_absurd_year_is_rejected(self):
        with pytest.raises(ValueError, match="Year"):
            birthdays_repo.validate(7, 4, 1703)


class TestDeletion:
    """Requirement: a deleted birthday is removed completely."""

    def test_deleting_removes_the_row(self, session):
        add_member(session, "U_ANA")
        add_birthday(session, "U_ANA", (6, 1))

        deleted = birthdays_repo.remove(session, "U_ANA")
        session.commit()

        assert deleted is True
        assert birthdays_repo.get(session, "U_ANA") is None

    def test_deleting_a_birthday_that_is_not_there_is_not_an_error(
        self, session
    ):
        add_member(session, "U_ANA")

        assert birthdays_repo.remove(session, "U_ANA") is False
