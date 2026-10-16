"""The /birthday slash command."""

from slack_bolt import App

from fiesta.slack.handlers.views import birthday_modal_for


def register(app: App) -> None:
    @app.command("/birthday")
    def _on_command(ack, body, client) -> None:
        # Slack cancels the command if it isn't acknowledged within three
        # seconds, so this happens before any other work.
        ack()
        open_modal(body=body, client=client)


def open_modal(*, body, client) -> None:
    client.views_open(
        trigger_id=body["trigger_id"],
        view=birthday_modal_for(body["user_id"]),
    )
