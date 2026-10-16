"""Sentry switches on only when it has somewhere to report to."""

import logging

import sentry_sdk

from fiesta import monitoring
from fiesta.config import load_config


def test_without_a_dsn_sentry_is_not_started(monkeypatch):
    calls = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))

    started = monitoring.init_sentry(load_config(require_slack=False))

    assert started is False
    assert calls == []


def test_a_dsn_starts_sentry_without_personal_data(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://key@o0.ingest.sentry.io/0")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "staging")
    calls = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))

    started = monitoring.init_sentry(load_config(require_slack=False))

    assert started is True
    [options] = calls
    assert options["dsn"] == "https://key@o0.ingest.sentry.io/0"
    assert options["environment"] == "staging"
    assert options["release"] is None or options["release"].isalnum()
    assert options["send_default_pii"] is False


def test_an_explicit_release_wins_over_git(monkeypatch):
    """The Docker image has no .git, so its build sets the release instead."""
    monkeypatch.setenv("SENTRY_DSN", "https://key@o0.ingest.sentry.io/0")
    monkeypatch.setenv("SENTRY_RELEASE", "abc1234")
    calls = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))

    monitoring.init_sentry(load_config(require_slack=False))

    assert calls[0]["release"] == "abc1234"


def test_outside_a_git_checkout_the_release_is_left_unset(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)

    assert monitoring._git_short_sha() is None


def _log_hint(message: str) -> dict:
    record = logging.LogRecord(
        "slack_bolt", logging.ERROR, __file__, 0, message, None, None
    )
    return {"log_record": record}


def test_socket_mode_ping_failures_are_not_reported():
    """The Socket Mode client reconnects on its own after these."""
    message = (
        "Failed to check the state of sock (session id: abc, "
        "error: BrokenPipeError, message: [Errno 32] Broken pipe)"
    )

    assert monitoring._drop_socket_noise({}, _log_hint(message)) is None


def test_other_errors_are_still_reported():
    event = {"level": "error"}

    assert monitoring._drop_socket_noise(event, _log_hint("Boom")) is event
    assert monitoring._drop_socket_noise(event, {}) is event
