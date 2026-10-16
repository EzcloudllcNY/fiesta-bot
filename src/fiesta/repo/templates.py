"""Congratulation texts."""

from sqlmodel import Session, col, select

from fiesta.models import CongratsTemplate

PLACEHOLDER = "{user}"


def enabled(session: Session) -> list[CongratsTemplate]:
    statement = select(CongratsTemplate).where(
        col(CongratsTemplate.enabled) == True,  # noqa: E712
    )
    return list(session.exec(statement))


def get(session: Session, template_id: int) -> CongratsTemplate | None:
    return session.get(CongratsTemplate, template_id)


def add(session: Session, *, text: str) -> CongratsTemplate:
    _check_placeholder(text)
    template = CongratsTemplate(text=text)
    session.add(template)
    return template


def update(
    session: Session,
    template: CongratsTemplate,
    *,
    text: str | None = None,
    enabled: bool | None = None,
) -> CongratsTemplate:
    """Change the given fields; a None leaves that field as it is."""
    if text is not None:
        _check_placeholder(text)
        template.text = text
    if enabled is not None:
        template.enabled = enabled
    session.add(template)
    return template


def remove(session: Session, template: CongratsTemplate) -> None:
    session.delete(template)


def all_templates(session: Session) -> list[CongratsTemplate]:
    return list(
        session.exec(
            select(CongratsTemplate).order_by(col(CongratsTemplate.id))
        )
    )


def _check_placeholder(text: str) -> None:
    if PLACEHOLDER not in text:
        raise ValueError(f"A template must contain {PLACEHOLDER}")
