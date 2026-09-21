"""Notification transport and entrypoint checks without external delivery."""

import logging
from unittest.mock import Mock

import pytest

from hyperliquid_candles import app, notifications


def test_apprise_posts_to_stored_configuration_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical Apprise URLs add /notify/ and impose network timeouts."""
    post = Mock(return_value=Mock(status_code=200, content=b"{}"))
    monkeypatch.setattr("apprise.plugins.apprise_api.requests.post", post)
    notifier = notifications.Notifications("apprise://apprise-api:8000/" + "a" * 64)
    notifier.send("startup", "test")
    notifier.flush_pending()
    assert post.call_args.args[0] == "http://apprise-api:8000/notify/" + "a" * 64
    assert post.call_args.kwargs["timeout"] == (5.0, 5.0)


def test_rate_limit_excludes_secrets_and_notification_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warning bursts cannot suppress errors, leak arguments, or recurse."""
    notifier = notifications.Notifications("")
    sent = Mock()
    monkeypatch.setattr(notifier, "send", sent)
    monkeypatch.setattr(notifications.time, "monotonic", lambda: 1000)
    warning = logging.LogRecord(
        "hyperliquid_candles.storage",
        logging.WARNING,
        "",
        1,
        "ClickHouse not ready: %s",
        ("secret-password",),
        None,
    )
    failure = logging.LogRecord(
        "hyperliquid_candles.app",
        logging.ERROR,
        "",
        1,
        "Symbol %s failed",
        ("BTC",),
        None,
    )
    notifier.emit(warning)
    notifier.emit(warning)
    notifier.emit(failure)
    notifier.emit(
        logging.LogRecord(
            notifications.__name__, logging.WARNING, "", 1, "delivery failed", (), None
        )
    )
    assert sent.call_count == 2
    assert "secret-password" not in str(sent.call_args_list)
    monkeypatch.setattr(notifications.time, "monotonic", lambda: 1900)
    notifier.emit(warning)
    assert sent.call_count == 3


def test_delivery_exception_is_fail_open(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Transport errors do not leak destinations or stop the caller."""
    notifier = notifications.Notifications("apprise://localhost:8000/test-key")
    monkeypatch.setattr(
        notifier._client, "notify", Mock(side_effect=RuntimeError("secret-url"))
    )
    notifier.send("test", "test")
    notifier.flush_pending()
    assert notifier._pending.unfinished_tasks == 0
    assert "secret-url" not in caplog.text
    assert "delivery failed" in caplog.text


def test_startup_alert_precedes_configuration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid service configuration still triggers startup and failure notices."""
    notifier = Mock()
    monkeypatch.setattr(app, "configure_notifications", lambda: notifier)
    monkeypatch.setattr(app, "load_settings", Mock(side_effect=ValueError("secret")))
    with pytest.raises(ValueError):
        app.main()
    assert notifier.send.call_count == 2
    assert "starting" in notifier.send.call_args_list[0].args[0]
    assert "stopped" in notifier.send.call_args_list[1].args[0]
    assert "secret" not in str(notifier.send.call_args_list)
    notifier.flush_pending.assert_called_once()


def test_env_file_overrides_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deployment's mounted .env is authoritative without secret interpolation."""
    monkeypatch.setenv("APPRISE_URL", "apprise://localhost:8000/test-key")
    monkeypatch.setattr(
        notifications, "dotenv_values", Mock(return_value={"APPRISE_URL": ""})
    )
    notifier = notifications.configure_notifications()
    try:
        assert not notifier.enabled
        notifications.dotenv_values.assert_called_once_with(
            notifications.PROJECT_ROOT / ".env", interpolate=False
        )
    finally:
        logging.getLogger(notifications.PROJECT_LOGGER).removeHandler(notifier)
