"""Best-effort startup and operational alerts through a stored Apprise API key."""

from __future__ import annotations

import logging
import os
import threading
import time
from queue import Full, Queue
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import apprise
from dotenv import dotenv_values

from hyperliquid_candles.config import PROJECT_ROOT

LOGGER = logging.getLogger(__name__)
ALERT_COOLDOWN_SECONDS = 900
NOTIFICATION_TIMEOUT_SECONDS = 5
SHUTDOWN_WAIT_SECONDS = 10
SHUTDOWN_POLL_SECONDS = 0.05
MAX_PENDING_NOTIFICATIONS = 8
PROJECT_LOGGER = "hyperliquid_candles"


class Notifications(logging.Handler):
    """Send bounded, asynchronous operational alerts without blocking ingestion.

    Parameters
    ----------
    url : str
        Apprise API URL, ``apprise://host:port/key``. Empty disables delivery.

    Notes
    -----
    Warning and error groups each send at most once per fifteen minutes per
    process. Messages use log templates, not formatted arguments or tracebacks,
    so database credentials and request URLs cannot enter notification bodies.
    A daemon worker and bounded queue keep network outages off the data path.
    """

    def __init__(self, url: str) -> None:
        super().__init__(logging.WARNING)
        self._last_alert: dict[str, float] = {}
        self._pending: Queue[tuple[str, str, str]] = Queue(MAX_PENDING_NOTIFICATIONS)
        self._client = apprise.Apprise()
        self.enabled = False
        self._worker: threading.Thread | None = None
        # Apprise may log destinations and server responses; keep those private.
        apprise_logger = logging.getLogger("apprise")
        apprise_logger.handlers = [logging.NullHandler()]
        apprise_logger.propagate = False
        if not url:
            LOGGER.warning("APPRISE_URL is empty; notifications are disabled")
            return
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in {"apprise", "apprises"}:
                raise ValueError("Use an Apprise API URL")
            query = dict(parse_qsl(parsed.query))
            query.update(
                cto=str(NOTIFICATION_TIMEOUT_SECONDS),
                rto=str(NOTIFICATION_TIMEOUT_SECONDS),
            )
            configured_url = urlunsplit(parsed._replace(query=urlencode(query)))
            self.enabled = self._client.add(configured_url)
        except Exception:
            self.enabled = False
        if not self.enabled:
            LOGGER.warning("APPRISE_URL is invalid; notifications are disabled")
            return
        self._worker = threading.Thread(
            target=self._deliver, daemon=True, name="apprise-notifications"
        )
        self._worker.start()

    def send(self, title: str, body: str, severity: str = "info") -> None:
        """Queue a safe, application-authored message; never wait on the network."""
        if not self.enabled:
            return
        try:
            self._pending.put_nowait((title, body, severity))
        except Full:
            LOGGER.warning("Notification queue is full; an alert was dropped")

    def emit(self, record: logging.LogRecord) -> None:
        """Forward project warnings/errors with a separate cooldown per severity."""
        if record.name == __name__ or not record.name.startswith(PROJECT_LOGGER + "."):
            return
        severity = "failure" if record.levelno >= logging.ERROR else "warning"
        now = time.monotonic()
        if now - self._last_alert.get(severity, float("-inf")) < ALERT_COOLDOWN_SECONDS:
            return
        self._last_alert[severity] = now
        # Only static log templates travel outside the service. Values and
        # exception text remain in the existing local logs for investigation.
        self.send(
            "hyperliquid-long-term: " + severity,
            f"{record.name}: {record.msg}\nCheck the container logs. Repeated alerts are limited to one per 15 minutes per severity.",
            severity,
        )

    def _deliver(self) -> None:
        """Drain queued messages; delivery failures cannot crash collection."""
        while True:
            title, body, severity = self._pending.get()
            try:
                delivered = self._client.notify(
                    title=title, body=body, notify_type=severity
                )
                if not delivered:
                    LOGGER.warning(
                        "Apprise notification delivery failed; check service connectivity"
                    )
            except Exception:
                LOGGER.warning(
                    "Apprise notification delivery failed; check service connectivity"
                )
            finally:
                self._pending.task_done()

    def flush_pending(self) -> None:
        """Give fatal-startup messages a bounded opportunity to leave the process."""
        deadline = time.monotonic() + SHUTDOWN_WAIT_SECONDS
        while self._pending.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(SHUTDOWN_POLL_SECONDS)


def configure_notifications() -> Notifications:
    """Read the authoritative project .env and attach one project log handler."""
    file_values = dotenv_values(PROJECT_ROOT / ".env", interpolate=False)
    url = file_values.get("APPRISE_URL", os.environ.get("APPRISE_URL", "")) or ""
    notifier = Notifications(url)
    project_logger = logging.getLogger(PROJECT_LOGGER)
    for handler in list(project_logger.handlers):
        if isinstance(handler, Notifications):
            project_logger.removeHandler(handler)
    project_logger.addHandler(notifier)
    return notifier
