"""Email and webhook notification dispatch for lux-mon alerts.

Uses authenticated SMTP relay only (no direct delivery) to avoid spam/blacklist
issues, plus an optional webhook fallback/alternative.
"""

from __future__ import annotations

import json
import logging
import re
import smtplib
import ssl
import time
from email.mime.text import MIMEText
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("luxmon.notifiers")


class Notifiers:
    """Send alert state changes via email and/or webhook."""

    def __init__(self, cfg: Any):
        self.cfg = cfg
        self._last_sent: Dict[str, float] = {}
        self._min_interval_sec = 300  # throttle repeated state emails/webhooks

    def _cfg_bool(self, name: str, default: bool = False) -> bool:
        val = getattr(self.cfg, name, default)
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("true", "1", "yes", "on")

    def _cfg_str(self, name: str, default: str = "") -> str:
        val = getattr(self.cfg, name, default)
        return str(val) if val is not None else default

    def _cfg_int(self, name: str, default: int = 0) -> int:
        try:
            return int(getattr(self.cfg, name, default) or default)
        except (TypeError, ValueError):
            return default

    def _rate_ok(self, name: str) -> bool:
        now = time.time()
        last = self._last_sent.get(name, 0)
        if now - last < self._min_interval_sec:
            return False
        self._last_sent[name] = now
        return True

    def send(
        self,
        alert_name: str,
        active: bool,
        value: float,
        message: str,
    ) -> None:
        if not self._cfg_bool("alerts_enabled"):
            return

        subject, body = self._format(alert_name, active, value, message)

        if self._cfg_bool("alerts_email_enabled"):
            try:
                self._send_email(subject, body, alert_name)
            except Exception:
                logger.exception("Email notify failed for %s", alert_name)

        if self._cfg_bool("alerts_webhook_enabled"):
            try:
                self._send_webhook(alert_name, active, value, message, subject, body)
            except Exception:
                logger.exception("Webhook notify failed for %s", alert_name)

    def _format(
        self,
        alert_name: str,
        active: bool,
        value: float,
        message: str,
    ) -> Tuple[str, str]:
        state_str = "ACTIVE" if active else "CLEARED"
        subject = f"[lux-mon] {alert_name.replace('_', ' ').title()} {state_str}"
        ts = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        body = (
            f"lux-mon alert notification\n"
            f"-------------------------\n"
            f"Alert:    {alert_name}\n"
            f"State:    {state_str}\n"
            f"Value:    {value}\n"
            f"Message:  {message}\n"
            f"Time:     {ts}\n"
        )
        return subject, body

    def _send_email(self, subject: str, body: str, alert_name: str) -> None:
        host = self._cfg_str("alerts_email_smtp_host")
        port = self._cfg_int("alerts_email_smtp_port", 587)
        username = self._cfg_str("alerts_email_username")
        password = self._cfg_str("alerts_email_password")
        from_addr = self._cfg_str("alerts_email_from")
        to_addrs = self._cfg_str("alerts_email_to")

        if not host or not username or not password or not from_addr or not to_addrs:
            logger.warning("Email notifier misconfigured; skipping %s", alert_name)
            return

        if not self._rate_ok(f"email:{alert_name}"):
            logger.debug("Email for %s throttled", alert_name)
            return

        recipients = [a.strip() for a in re.split(r"[,;]", to_addrs) if a.strip()]
        if not recipients:
            return

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(recipients)

        use_tls = self._cfg_bool("alerts_email_tls", True)
        context = ssl.create_default_context()

        if use_tls and port != 465:
            with smtplib.SMTP(host, port, timeout=30) as server:
                server.starttls(context=context)
                server.login(username, password)
                server.sendmail(from_addr, recipients, msg.as_string())
        else:
            with smtplib.SMTP_SSL(host, port, timeout=30, context=context) as server:
                server.login(username, password)
                server.sendmail(from_addr, recipients, msg.as_string())

        logger.info("Email alert sent: %s to %s", alert_name, recipients)

    def _send_webhook(
        self,
        alert_name: str,
        active: bool,
        value: float,
        message: str,
        subject: str,
        body: str,
    ) -> None:
        url = self._cfg_str("alerts_webhook_url")
        if not url:
            logger.warning("Webhook URL not configured; skipping %s", alert_name)
            return

        if not self._rate_ok(f"webhook:{alert_name}"):
            logger.debug("Webhook for %s throttled", alert_name)
            return

        payload = json.dumps({
            "alert": alert_name,
            "active": active,
            "state": "ON" if active else "OFF",
            "value": value,
            "message": message,
            "subject": subject,
            "body": body,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }).encode("utf-8")

        req = Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "lux-mon/1.0",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=30) as resp:
                resp.read()
        except HTTPError as e:
            logger.warning("Webhook returned %s for %s", e.code, alert_name)
        except URLError as e:
            logger.warning("Webhook failed for %s: %s", alert_name, e.reason)

        logger.info("Webhook alert sent: %s to %s", alert_name, url)


def from_config(cfg: Any) -> Notifiers:
    """Factory for creating a Notifiers instance from any config-like object."""
    return Notifiers(cfg)
