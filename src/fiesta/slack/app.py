"""The Bolt app, running over Socket Mode.

Socket Mode carries everything this bot needs on one outbound WebSocket: events,
the slash command, block actions and modal submissions. Nothing dials in, so
there is no public URL, no request-signature verification and no ports to
publish.
"""

import logging

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from fiesta.config import Config
from fiesta.slack.handlers import register_all

log = logging.getLogger(__name__)


def build_app(config: Config) -> App:
    app = App(
        token=config.slack_bot_token, logger=logging.getLogger("slack_bolt")
    )
    register_all(app)
    return app


def build_handler(app: App, config: Config) -> SocketModeHandler:
    return SocketModeHandler(app, config.slack_app_token)
