"""Notification interface and offline mock provider."""

from abc import ABC, abstractmethod


class NotificationProvider(ABC):
    """Adapter contract for future email/webhook/SMS integrations."""
    @abstractmethod
    def notify(self, alert):
        """Return a JSON-safe delivery status mapping."""


class MockNotificationProvider(NotificationProvider):
    """Network-free provider that records attempts for tests and demonstrations."""
    name = "mock_notification"

    def __init__(self, fail=False, failure_message="simulated notification failure"):
        self.fail = fail
        self.failure_message = failure_message
        self.attempts = []

    def notify(self, alert):
        alert_id = alert.get("alert_id")
        attempt = {"alert_id": alert_id, "attempt": len(self.attempts) + 1,
                   "provider": self.name, "status": "failed" if self.fail else "sent"}
        if self.fail:
            attempt["error"] = self.failure_message[:250]
        self.attempts.append(dict(attempt))
        return attempt
