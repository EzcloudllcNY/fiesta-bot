"""Choosing and filling in a congratulation text."""

import random

from sqlmodel import Session

from fiesta.repo import templates as templates_repo
from fiesta.slack.blocks import mention

FALLBACK = ":tada: Happy birthday, {user}!"
"""Used when the template table is empty. A celebration should never be skipped
just because an admin deleted every text."""


def render(session: Session, user_id: str) -> str:
    """A ready-to-post congratulation for one member."""
    choices = templates_repo.enabled(session)
    text = random.choice(choices).text if choices else FALLBACK
    return text.replace(templates_repo.PLACEHOLDER, mention(user_id))
