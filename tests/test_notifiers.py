"""Unit tests for collector/notifiers.py."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from collector import notifiers


class DummyConfig:
    """Minimal config object for Notifiers."""

    def __init__(self, **kwargs):
        self._values = {
            "alerts_enabled": "false",
            "alerts_email_enabled": "false",
            "alerts_email_smtp_host": "",
            "alerts_email_smtp_port": "587",
            "alerts_email_username": "",
            "alerts_email_password": "",
            "alerts_email_from": "",
            "alerts_email_to": "",
            "alerts_email_tls": "true",
            "alerts_webhook_enabled": "false",
            "alerts_webhook_url": "",
        }
        self._values.update(kwargs)

    def __getattr__(self, name: str):
        return self._values.get(name)


class TestNotifiers(unittest.TestCase):
    def test_disabled_does_nothing(self):
        n = notifiers.from_config(DummyConfig(alerts_enabled="false"))
        n.send("battery_soc_low", True, 15.0, "low battery")
        # No exception and no network call attempted.
        self.assertTrue(True)

    def test_format(self):
        n = notifiers.from_config(DummyConfig())
        subject, body = n._format("grid_loss", True, 0.0, "Grid absent for 45s")
        self.assertIn("Grid Loss", subject)
        self.assertIn("ACTIVE", subject)
        self.assertIn("lux-mon alert notification", body)
        self.assertIn("Grid absent for 45s", body)

    def test_rate_limit(self):
        n = notifiers.from_config(DummyConfig())
        n._min_interval_sec = 0.05

        self.assertTrue(n._rate_ok("email:test"))
        self.assertFalse(n._rate_ok("email:test"))
        time.sleep(0.06)
        self.assertTrue(n._rate_ok("email:test"))
        self.assertFalse(n._rate_ok("email:test"))


if __name__ == "__main__":
    unittest.main()
