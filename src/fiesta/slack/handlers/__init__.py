"""Wiring every Slack handler onto the Bolt app."""

from slack_bolt import App

from fiesta.slack.handlers import actions, commands, events, views


def register_all(app: App) -> None:
    commands.register(app)
    events.register(app)
    actions.register(app)
    views.register(app)
