"""Curated GIF pool — phase 2.

Kept behind the same interface the celebration service already calls, so turning
GIFs on later is a settings change plus some rows, not a code change.
"""

import random

from sqlalchemy import func
from sqlmodel import Session, col, select

from fiesta import clock
from fiesta.models import MAX_FAILURES, Gif

ROTATION_SLICE = 10
"""At most this many least-recently-used GIFs are considered, and one of them is
taken at random. Always taking the single oldest would make the order fully
predictable; taking from the whole pool would let the same GIF come up twice
running."""


def pick(session: Session) -> Gif | None:
    """A usable GIF not shown lately, or None if the pool is empty."""
    usable = (
        col(Gif.enabled) == True,  # noqa: E712
        Gif.fail_count < MAX_FAILURES,
    )

    # The slice is capped at half the pool, so a small pool still rotates. With
    # a flat slice of ten, a pool of ten or fewer would be picked from at random
    # every time and the least-recently-used ordering would do nothing at all.
    pool_size = session.exec(
        select(func.count()).select_from(Gif).where(*usable)
    ).one()
    if not pool_size:
        return None
    slice_size = max(1, min(ROTATION_SLICE, pool_size // 2))

    statement = (
        select(Gif)
        .where(*usable)
        .order_by(col(Gif.last_used_at).is_(None).desc(), col(Gif.last_used_at))
        .limit(slice_size)
    )
    candidates = list(session.exec(statement))
    if not candidates:
        return None

    chosen = random.choice(candidates)
    chosen.last_used_at = clock.now_utc()
    session.add(chosen)
    return chosen


def get(session: Session, gif_id: int) -> Gif | None:
    return session.get(Gif, gif_id)


def by_url(session: Session, url: str) -> Gif | None:
    return session.exec(select(Gif).where(col(Gif.url) == url)).first()


def all_gifs(session: Session) -> list[Gif]:
    return list(session.exec(select(Gif).order_by(col(Gif.id))))


def add(session: Session, *, url: str, tags: str | None = None) -> Gif:
    _check_unused(session, url)
    gif = Gif(url=url, tags=tags)
    session.add(gif)
    return gif


def update(
    session: Session,
    gif: Gif,
    *,
    url: str | None = None,
    tags: str | None = None,
    enabled: bool | None = None,
) -> Gif:
    """Change the given fields; a None leaves that field as it is.

    An empty ``tags`` clears them. A new URL starts with a clean failure count,
    since the failures belonged to the old one.
    """
    if url is not None and url != gif.url:
        _check_unused(session, url)
        gif.url = url
        gif.fail_count = 0
    if tags is not None:
        gif.tags = tags or None
    if enabled is not None:
        gif.enabled = enabled
    session.add(gif)
    return gif


def remove(session: Session, gif: Gif) -> None:
    session.delete(gif)


def _check_unused(session: Session, url: str) -> None:
    if by_url(session, url) is not None:
        raise ValueError(f"{url} is already in the pool")


def record_failure(session: Session, url: str) -> None:
    """Count a GIF that Slack could not render; enough of these retire it."""
    gif = by_url(session, url)
    if gif is not None:
        gif.fail_count += 1
        session.add(gif)
