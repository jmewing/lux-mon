"""MQTT command topics for bi-directional control of lux-mon settings.

Subscribes to:
  <prefix>/<device_id>/set/<setting>

and, in the future:
  <prefix>/<device_id>/inverter/<param>

Currently supports changing lux-mon settings stored in MariaDB (safe,
no inverter writes). Inverter parameter commands are accepted and
logged but not yet executed — they require Modbus write support which is
planned for the Modbus RTU/RS485 transport work.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

import pymysql

from collector.settings import get_all, set_, DEFAULTS, SETTING_META

logger = logging.getLogger("luxmon.mqtt_commands")

# Settings that can be changed at runtime via MQTT.
# These are all stored in MariaDB and take effect on the next collector cycle.
CONTROLLABLE_SETTINGS = {
    "pv_max_power",
    "grid_max_power",
    "eps_max_power",
    "charge_max_power",
    "discharge_max_power",
    "battery_capacity",
    "battery_metric",
    "dashboard_refresh_sec",
    "chart_default_hours",
    "temperature_unit",
    "alerts_enabled",
    "alerts_soc_low",
    "alerts_soc_critical",
    "alerts_battery_temp_high",
    "alerts_inverter_temp_high",
    "alerts_grid_lost_threshold_sec",
}

# Inverter-side parameters that require Modbus writes. Not implemented yet.
INVERTER_PARAMS = {
    # placeholder mapping: param_name -> (register_address, scale, min, max)
}


def _build_conn(host: str, port: int, user: str, password: str, database: str):
    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        autocommit=True,
    )


def _validate_and_convert(name: str, raw: str) -> tuple[bool, str, Optional[str]]:
    """Validate a new setting value. Returns (ok, converted_value, error_message)."""
    meta = SETTING_META.get(name)
    if not meta:
        return True, raw, None

    stype = meta.get("type", "text")
    if stype == "number":
        try:
            value = float(raw)
        except ValueError:
            return False, raw, f"{name} must be a number"
        mn = meta.get("min")
        mx = meta.get("max")
        if mn is not None and value < mn:
            return False, raw, f"{name} must be >= {mn}"
        if mx is not None and value > mx:
            return False, raw, f"{name} must be <= {mx}"
        # Preserve original precision if an integer was supplied and step is whole
        step = meta.get("step")
        if step is not None and step == int(step) and float(raw) == int(float(raw)):
            return True, str(int(value)), None
        return True, str(value), None

    if stype == "select":
        options = {opt[0] for opt in meta.get("options", [])}
        if raw not in options:
            return False, raw, f"{name} must be one of {sorted(options)}"
        return True, raw, None

    if stype == "checkbox":
        if raw.lower() not in ("true", "false", "1", "0", "on", "off"):
            return False, raw, f"{name} must be a boolean"
        return True, "true" if raw.lower() in ("true", "1", "on") else "false", None

    return True, raw, None


class MqttCommands:
    """Subscribe to MQTT command topics and apply setting changes."""

    def __init__(
        self,
        client: Any,
        prefix: str,
        device_id: str,
        db_host: str,
        db_port: int,
        db_user: str,
        db_password: str,
        db_name: str,
        table_prefix: str = "lux_",
        cfg: Any = None,
    ):
        self.client = client
        self.cfg = cfg
        self.prefix = prefix
        self.device_id = device_id
        self.set_topic = f"{prefix}/{device_id}/set/+"
        self.inverter_topic = f"{prefix}/{device_id}/inverter/+"
        self.ack_topic = f"{prefix}/{device_id}/ack"
        self.error_topic = f"{prefix}/{device_id}/error"
        self._db_args = (db_host, db_port, db_user, db_password, db_name)
        self._table_prefix = table_prefix

        self.client.message_callback_add(self.set_topic, self._on_set)
        self.client.message_callback_add(self.inverter_topic, self._on_inverter)
        self.client.subscribe(f"{prefix}/{device_id}/set/#")
        self.client.subscribe(f"{prefix}/{device_id}/inverter/#")
        logger.info("MQTT command topics subscribed: %s, %s", self.set_topic, self.inverter_topic)

    def _publish(self, topic: str, payload: dict) -> None:
        try:
            self.client.publish(topic, json.dumps(payload), qos=1, retain=False)
        except Exception:
            logger.exception("Failed to publish MQTT ack to %s", topic)

    def _on_set(self, client: Any, userdata: Any, msg: Any) -> None:
        """Handle <prefix>/<device_id>/set/<setting> messages."""
        topic = msg.topic
        name = topic.split("/")[-1]
        raw_payload = msg.payload.decode("utf-8", errors="ignore").strip()
        logger.info("MQTT set request: %s = %s", name, raw_payload)

        if name not in CONTROLLABLE_SETTINGS:
            self._publish(self.error_topic, {"name": name, "error": "unknown or read-only setting"})
            return

        ok, value, error = _validate_and_convert(name, raw_payload)
        if not ok:
            self._publish(self.error_topic, {"name": name, "error": error})
            return

        conn = None
        try:
            conn = _build_conn(*self._db_args)
            set_(conn, name, value)
            logger.info("Updated setting %s to %s via MQTT", name, value)
            if self.cfg is not None and hasattr(self.cfg, name):
                setattr(self.cfg, name, value)
                logger.info("Updated running config %s to %s", name, value)
            self._publish(self.ack_topic, {"name": name, "value": value, "source": "mqtt"})
        except Exception as exc:
            logger.exception("Failed to apply MQTT setting %s", name)
            self._publish(self.error_topic, {"name": name, "error": str(exc)})
        finally:
            if conn:
                conn.close()

    def _on_inverter(self, client: Any, userdata: Any, msg: Any) -> None:
        """Handle <prefix>/<device_id>/inverter/<param> messages.

        Inverter writes are not yet implemented — they require Modbus
        function-code 0x06/0x10 support and a verified holding-register map.
        """
        topic = msg.topic
        param = topic.split("/")[-1]
        raw_payload = msg.payload.decode("utf-8", errors="ignore").strip()
        logger.info("MQTT inverter command received (not implemented): %s = %s", param, raw_payload)
        self._publish(
            self.error_topic,
            {"param": param, "error": "inverter writes not yet implemented", "value": raw_payload},
        )
