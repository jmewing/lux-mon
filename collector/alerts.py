"""Alert threshold evaluation for lux-mon.

Publishes alert state changes to MQTT (for Home Assistant binary_sensor discovery)
and writes alert events to MariaDB.
"""

import json
import logging
import time
from typing import Any, Dict, Optional

from collector import notifiers

logger = logging.getLogger("luxmon.alerts")


class Alerts:
    """Evaluate inverter/battery alert rules and publish state changes."""

    def __init__(self, cfg: Any, mqtt_client: Any = None, mariadb_conn: Any = None):
        self.cfg = cfg
        self._mqtt_client = mqtt_client
        self._mariadb_conn = mariadb_conn
        self._notifiers = notifiers.from_config(cfg)
        self._state: Dict[str, bool] = {}
        self._grid_lost_since: Optional[float] = None
        self._ha_announced: set = set()
        self._init_mariadb()
        self._init_mqtt()

    def _init_mariadb(self) -> None:
        if not self._mariadb_conn:
            return
        try:
            with self._mariadb_conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS lux_alerts (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        ts DATETIME DEFAULT CURRENT_TIMESTAMP,
                        alert_name VARCHAR(64) NOT NULL,
                        state VARCHAR(10) NOT NULL,
                        value DOUBLE,
                        message VARCHAR(255),
                        INDEX idx_name_ts (alert_name, ts)
                    ) ENGINE=InnoDB
                    """
                )
        except Exception:
            logger.exception("Failed to initialize alerts table")

    def _init_mqtt(self) -> None:
        if self._mqtt_client or not getattr(self.cfg, "mqtt_enabled", False):
            return
        try:
            import paho.mqtt.client as mqtt
            client = mqtt.Client()
            username = getattr(self.cfg, "mqtt_username", "") or ""
            password = getattr(self.cfg, "mqtt_password", "") or ""
            if username:
                client.username_pw_set(username, password)
            host = getattr(self.cfg, "mqtt_host", "localhost") or "localhost"
            port = int(getattr(self.cfg, "mqtt_port", 1883) or 1883)
            client.connect(host, port, 60)
            client.loop_start()
            self._mqtt_client = client
            logger.info("Alerts MQTT client connected")
        except Exception:
            logger.exception("Failed to connect alerts MQTT client")

    def _cfg_float(self, name: str, default: float) -> float:
        try:
            return float(getattr(self.cfg, name, default) or default)
        except (TypeError, ValueError):
            return default

    def _cfg_bool(self, name: str, default: bool = False) -> bool:
        val = getattr(self.cfg, name, default)
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("true", "1", "yes", "on")

    def evaluate(self, decoded: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Evaluate all rules. Returns current alert states."""
        if not self._cfg_bool("alerts_enabled"):
            return {}

        results: Dict[str, tuple] = {}
        now = time.time()

        soc = _num(decoded, "soc")
        bat_temp = _num(decoded, "temp_battery")
        inv_temp = _num(decoded, "temp_inverter")
        grid_v = _num(decoded, "grid_voltage_r")
        grid_f = _num(decoded, "grid_frequency")
        fault = _num(decoded, "fault") or _num(decoded, "fault_code") or _num(decoded, "error")

        soc_low = self._cfg_float("alerts_soc_low", 20.0)
        soc_critical = self._cfg_float("alerts_soc_critical", 10.0)
        bat_temp_high = self._cfg_float("alerts_battery_temp_high", 50.0)
        inv_temp_high = self._cfg_float("alerts_inverter_temp_high", 60.0)
        grid_threshold = self._cfg_float("alerts_grid_lost_threshold_sec", 30.0)

        results["battery_soc_low"] = (
            soc is not None and soc <= soc_low,
            soc or 0.0,
            f"Battery SOC {soc}% at or below {soc_low}%"
        )
        results["battery_soc_critical"] = (
            soc is not None and soc <= soc_critical,
            soc or 0.0,
            f"Battery SOC {soc}% at or below {soc_critical}%"
        )
        results["battery_temp_high"] = (
            bat_temp is not None and bat_temp >= bat_temp_high,
            bat_temp or 0.0,
            f"Battery temperature {bat_temp}°C at or above {bat_temp_high}°C"
        )
        results["inverter_temp_high"] = (
            inv_temp is not None and inv_temp >= inv_temp_high,
            inv_temp or 0.0,
            f"Inverter temperature {inv_temp}°C at or above {inv_temp_high}°C"
        )

        # Grid loss: voltage or frequency near zero for threshold duration
        grid_present = (grid_v is not None and grid_v > 80) and (grid_f is not None and grid_f > 45)
        if grid_present:
            self._grid_lost_since = None
            results["grid_loss"] = (False, grid_v or 0.0, "Grid present")
        else:
            if self._grid_lost_since is None:
                self._grid_lost_since = now
            lost_for = now - self._grid_lost_since
            results["grid_loss"] = (
                lost_for >= grid_threshold,
                grid_v or 0.0,
                f"Grid absent for {lost_for:.0f}s"
            )

        if fault is not None and fault != 0:
            results["fault_active"] = (True, fault, f"Fault code {fault} active")
        else:
            results["fault_active"] = (False, fault or 0.0, "No active fault")

        active: Dict[str, Dict[str, Any]] = {}
        for name, (is_active, value, message) in results.items():
            was_active = self._state.get(name, False)
            self._state[name] = is_active
            active[name] = {
                "active": is_active,
                "value": value,
                "message": message,
                "since": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            if is_active != was_active:
                self._publish(name, is_active, value, message)
                self._notifiers.send(name, is_active, value, message)

        return active

    def _publish(self, name: str, active: bool, value: float, message: str) -> None:
        state_str = "ON" if active else "OFF"
        logger.warning("Alert %s: %s (%s)", name, state_str, message)

        if self._mqtt_client:
            topic_prefix = getattr(self.cfg, "mqtt_topic_prefix", "luxmon") or "luxmon"
            device_id = getattr(self.cfg, "mqtt_device_id", "luxmon_solar") or "luxmon_solar"
            topic = f"{topic_prefix}/{device_id}/alert/{name}"
            payload = json.dumps({
                "state": state_str,
                "value": value,
                "message": message,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            try:
                self._mqtt_client.publish(topic, payload, qos=1, retain=True)
            except Exception:
                logger.exception("Failed to publish alert %s", name)

            if self._cfg_bool("mqtt_ha_discovery", True):
                self._publish_ha_discovery(name)

        if self._mariadb_conn:
            try:
                with self._mariadb_conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO lux_alerts (alert_name, state, value, message) VALUES (%s, %s, %s, %s)",
                        (name, state_str, value, message),
                    )
            except Exception:
                logger.exception("Failed to write alert %s to MariaDB", name)

    def _publish_ha_discovery(self, name: str) -> None:
        if name in self._ha_announced:
            return
        self._ha_announced.add(name)
        topic_prefix = getattr(self.cfg, "mqtt_topic_prefix", "luxmon") or "luxmon"
        device_id = getattr(self.cfg, "mqtt_device_id", "luxmon_solar") or "luxmon_solar"
        device_name = getattr(self.cfg, "mqtt_device_name", "luxmon") or "luxmon"
        ha_prefix = getattr(self.cfg, "mqtt_ha_prefix", "homeassistant") or "homeassistant"
        device = {
            "identifiers": [device_id],
            "name": device_name,
            "model": "lux-mon",
            "manufacturer": "lux-mon",
        }
        config = {
            "name": name.replace("_", " ").title(),
            "unique_id": f"{device_id}_alert_{name}",
            "state_topic": f"{topic_prefix}/{device_id}/alert/{name}",
            "value_template": "{{ value_json.state }}",
            "payload_on": "ON",
            "payload_off": "OFF",
            "device_class": "problem",
            "device": device,
        }
        topic = f"{ha_prefix}/binary_sensor/{device_id}_alert_{name}/config"
        try:
            self._mqtt_client.publish(topic, json.dumps(config), qos=1, retain=True)
            logger.info("Published HA discovery for alert %s", name)
        except Exception:
            logger.exception("Failed to publish HA alert discovery %s", name)

    def close(self) -> None:
        if self._mqtt_client:
            try:
                self._mqtt_client.loop_stop()
                self._mqtt_client.disconnect()
            except Exception:
                pass


def _num(decoded: Dict[str, Any], key: str) -> Optional[float]:
    info = decoded.get(key)
    if isinstance(info, dict):
        try:
            return float(info["value"])
        except (KeyError, TypeError, ValueError):
            pass
    try:
        return float(info)
    except (TypeError, ValueError):
        return None
