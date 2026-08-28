"""Tests for the SolarAssistant-style automation v2 engine (nested rule table)."""

import json
import time
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from collector.automation import (
    Automation,
    AutomationEngine,
    Condition,
    Group,
    Range,
    _all_conditions_match,
    _clamp_to_register,
    _condition_matches,
    _engineering_to_raw,
    _find_matching_range,
    _parse_hhmm,
    _raw_to_engineering,
    _time_matches,
    TYPE_BATTERY_PROTECTION,
    TYPE_BATTERY_SOC,
    TYPE_NOTIFY,
    TYPE_RULE_TABLE,
)


class TestRegisterPrimitives(unittest.TestCase):
    def test_engineering_to_raw(self):
        meta = {"scale": 0.1, "min": 0, "max": 100}
        self.assertEqual(_engineering_to_raw(57.4, meta), 574)
        self.assertEqual(_engineering_to_raw(0.0, meta), 0)

    def test_raw_to_engineering(self):
        meta = {"scale": 0.1}
        self.assertAlmostEqual(_raw_to_engineering(574, meta), 57.4)

    def test_clamp_to_register(self):
        meta = {"min": 0, "max": 100}
        self.assertEqual(_clamp_to_register(-5, meta), 0)
        self.assertEqual(_clamp_to_register(150, meta), 100)
        self.assertEqual(_clamp_to_register(50, meta), 50)


class TestConditionMatching(unittest.TestCase):
    def test_time_range(self):
        with patch("collector.automation._now_in_tz") as mock_now:
            mock_now.return_value = datetime(2026, 8, 28, 2, 30)
            self.assertTrue(_time_matches("21:00-06:00", "time_of_day"))
            mock_now.return_value = datetime(2026, 8, 28, 12, 0)
            self.assertFalse(_time_matches("21:00-06:00", "time_of_day"))

    def test_day_of_week(self):
        with patch("collector.automation._now_in_tz") as mock_now:
            mock_now.return_value = datetime(2026, 8, 28, 12, 0)  # Friday
            self.assertTrue(_time_matches("Fri", "day_of_week"))
            self.assertFalse(_time_matches("Mon", "day_of_week"))

    def test_sensor_range(self):
        snapshot = {"soc": {"value": 22.0}, "battery_voltage": {"value": 51.0}}
        c = Condition(kind="battery_soc", min=0, max=25)
        self.assertTrue(_condition_matches(c, snapshot))
        c2 = Condition(kind="battery_soc", min=30, max=100)
        self.assertFalse(_condition_matches(c2, snapshot))

    def test_all_conditions(self):
        snapshot = {"soc": {"value": 22.0}}
        conds = [
            Condition(kind="battery_soc", min=0, max=25),
            Condition(kind="battery_voltage", min=48, max=54),
        ]
        with patch("collector.automation._sensor_value") as mock_val:
            mock_val.side_effect = lambda snap, s: {"soc": 22.0, "battery_voltage": 51.0}.get(s)
            self.assertTrue(_all_conditions_match(conds, snapshot))


class TestAutomationEngine(unittest.TestCase):
    def setUp(self):
        self.engine = AutomationEngine(
            db_host="localhost", db_port=3306, db_user="x",
            db_password="x", db_name="x", table_prefix="lux_",
        )
        self.settings = {
            "automation_enabled": "true",
            "automation_global_dry_run": "true",
            "automations_v2": "[]",
        }

    def _fake_db(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = None
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cur
        return conn, cur

    def test_save_and_load(self):
        auto = Automation(
            id="a1", name="Test", type=TYPE_RULE_TABLE,
            setting="grid_charge_current",
            group_kind="time_of_day", range_kind="battery_voltage",
            groups=[Group(
                condition=Condition(kind="time_of_day", value="00:00-06:00"),
                ranges=[Range(condition=Condition(kind="battery_voltage", min=0, max=44), action_value=125)],
            )],
        )
        conn, cur = self._fake_db()
        with patch("collector.automation.AutomationEngine._db", return_value=conn):
            self.engine.save([auto])
            calls = [c for c, _ in cur.execute.call_args_list]
            self.assertTrue(any("INSERT INTO lux_settings" in str(c) for c in calls))

    def test_evaluate_rule_table_dry_run(self):
        auto = Automation(
            id="a1", name="Night charge", type=TYPE_RULE_TABLE,
            setting="grid_charge_current",
            group_kind="time_of_day", range_kind="battery_voltage",
            groups=[Group(
                condition=Condition(kind="time_of_day", value="00:00-06:00"),
                ranges=[Range(condition=Condition(kind="battery_voltage", min=0, max=44), action_value=125)],
            )],
            enabled=True,
        )
        self.engine._last_eval["a1"] = False
        snapshot = {"battery_voltage": {"value": 40.0}}
        with patch("collector.automation._now_in_tz") as mock_now:
            mock_now.return_value = datetime(2026, 8, 28, 2, 0)
            with patch("collector.automation._write_holding_register") as mock_write:
                with patch("collector.automation.AutomationEngine._log"):
                    with patch.object(self.engine, "_get_setting", side_effect=lambda k, d="": self.settings.get(k, d)):
                        self.engine._evaluate_one(
                            auto, snapshot,
                            "192.168.1.100", 8000, "BJ123", "INV123",
                            dry_run=True, timezone="America/Chicago",
                        )
            mock_write.assert_not_called()
            self.assertTrue(self.engine._last_eval["a1"])

    def test_evaluate_battery_protection(self):
        auto = Automation(
            id="bp1", name="Low SOC shutdown", type=TYPE_BATTERY_PROTECTION,
            threshold=25, action_value=48.0, restore_value=50.0,
            enabled=True,
        )
        self.engine._last_eval["bp1"] = False
        snapshot = {"soc": {"value": 20.0}}
        with patch("collector.automation._write_holding_register") as mock_write:
            with patch("collector.automation.AutomationEngine._log"):
                with patch.object(self.engine, "_get_setting", side_effect=lambda k, d="": self.settings.get(k, d)):
                    self.engine._evaluate_one(
                        auto, snapshot,
                        "192.168.1.100", 8000, "BJ123", "INV123",
                        dry_run=True, timezone="America/Chicago",
                    )
            mock_write.assert_not_called()
            self.assertTrue(self.engine._last_eval["bp1"])


class TestRuleTableNested(unittest.TestCase):
    """Nested rule table: group (outer) -> range (inner) -> action value."""

    def setUp(self):
        self.engine = AutomationEngine(
            db_host="localhost", db_port=3306, db_user="x",
            db_password="x", db_name="x", table_prefix="lux_",
        )
        self.settings = {
            "automation_enabled": "true",
            "automation_global_dry_run": "true",
            "automations_v2": "[]",
        }

    def _make_auto(self, groups, restore_value=None):
        return Automation(
            id="grid_charge_current",
            name="Grid charge current",
            type=TYPE_RULE_TABLE,
            enabled=True,
            setting="grid_charge_current",
            group_kind="time_of_day",
            range_kind="battery_voltage",
            groups=groups,
            restore_value=restore_value,
        )

    def _run(self, auto, snapshot, now):
        self.engine._last_eval[auto.id] = False
        with patch("collector.automation._now_in_tz") as mock_now:
            mock_now.return_value = now
            with patch("collector.automation._write_holding_register") as mock_write:
                with patch("collector.automation.AutomationEngine._log"):
                    with patch.object(self.engine, "_get_setting", side_effect=lambda k, d="": self.settings.get(k, d)):
                        self.engine._evaluate_one(
                            auto, snapshot,
                            "192.168.1.100", 8000, "BJ123", "INV123",
                            dry_run=True, timezone="America/Chicago",
                        )
                return mock_write

    def _grid_charge_groups(self):
        """The exact Solar Assistant example: 3 time blocks, multiple voltage ranges."""
        return [
            Group(
                condition=Condition(kind="time_of_day", value="00:00-06:00"),
                ranges=[
                    Range(condition=Condition(kind="battery_voltage", min=0, max=44), action_value=125),
                    Range(condition=Condition(kind="battery_voltage", min=44, max=48), action_value=100),
                    Range(condition=Condition(kind="battery_voltage", min=48, max=52), action_value=85),
                    Range(condition=Condition(kind="battery_voltage", min=52, max=56), action_value=45),
                    Range(condition=Condition(kind="battery_voltage", min=56, max=58), action_value=5),
                ],
            ),
            Group(
                condition=Condition(kind="time_of_day", value="06:01-20:59"),
                ranges=[
                    Range(condition=Condition(kind="battery_voltage", min=0, max=59), action_value=0),
                ],
            ),
            Group(
                condition=Condition(kind="time_of_day", value="21:00-23:59"),
                ranges=[
                    Range(condition=Condition(kind="battery_voltage", min=0, max=56), action_value=100),
                    Range(condition=Condition(kind="battery_voltage", min=56, max=56), action_value=15),
                ],
            ),
        ]

    def test_find_matching_range(self):
        auto = self._make_auto(self._grid_charge_groups())
        # 02:00, voltage 45 -> group 00:00-06:00, range 44-48 -> 100A
        snapshot = {"battery_voltage": {"value": 45.0}}
        with patch("collector.automation._now_in_tz") as mock_now:
            mock_now.return_value = datetime(2026, 8, 28, 2, 0)
            match = _find_matching_range(auto, snapshot)
        self.assertIsNotNone(match)
        group, rng = match
        self.assertEqual(group.condition.value, "00:00-06:00")
        self.assertEqual(rng.action_value, 100)

    def test_find_matching_range_no_match(self):
        auto = self._make_auto(self._grid_charge_groups())
        # 12:00 (06:01-20:59 block), voltage 60 -> no range matches (max 59)
        snapshot = {"battery_voltage": {"value": 60.0}}
        with patch("collector.automation._now_in_tz") as mock_now:
            mock_now.return_value = datetime(2026, 8, 28, 12, 0)
            match = _find_matching_range(auto, snapshot)
        self.assertIsNone(match)

    def test_evaluate_writes_matching_range_value(self):
        auto = self._make_auto(self._grid_charge_groups())
        snapshot = {"battery_voltage": {"value": 45.0}}
        mock_write = self._run(auto, snapshot, datetime(2026, 8, 28, 2, 0))
        mock_write.assert_not_called()  # dry-run
        self.assertTrue(self.engine._last_eval[auto.id])

    def test_evaluate_no_match_restores(self):
        auto = self._make_auto(self._grid_charge_groups(), restore_value=0)
        self.engine._last_eval[auto.id] = True
        snapshot = {"battery_voltage": {"value": 60.0}}
        with patch("collector.automation._now_in_tz") as mock_now:
            mock_now.return_value = datetime(2026, 8, 28, 12, 0)
            with patch("collector.automation._write_holding_register") as mock_write:
                with patch("collector.automation.AutomationEngine._log"):
                    with patch.object(self.engine, "_get_setting", side_effect=lambda k, d="": self.settings.get(k, d)):
                        self.engine._evaluate_one(
                            auto, snapshot,
                            "192.168.1.100", 8000, "BJ123", "INV123",
                            dry_run=True, timezone="America/Chicago",
                        )
            mock_write.assert_not_called()  # dry-run
            self.assertFalse(self.engine._last_eval[auto.id])

    def test_serialization_roundtrip(self):
        auto = self._make_auto(self._grid_charge_groups())
        data = auto.to_dict()
        restored = Automation.from_dict(data)
        self.assertEqual(restored.setting, "grid_charge_current")
        self.assertEqual(restored.group_kind, "time_of_day")
        self.assertEqual(restored.range_kind, "battery_voltage")
        self.assertEqual(len(restored.groups), 3)
        self.assertEqual(len(restored.groups[0].ranges), 5)
        self.assertEqual(restored.groups[0].ranges[0].action_value, 125)
        self.assertEqual(restored.groups[0].condition.value, "00:00-06:00")

    def test_single_group_single_range(self):
        auto = self._make_auto([
            Group(
                condition=Condition(kind="time_of_day", value="00:00-23:59"),
                ranges=[Range(condition=Condition(kind="battery_voltage", min=0, max=59), action_value=85)],
            ),
        ])
        snapshot = {"battery_voltage": {"value": 50.0}}
        mock_write = self._run(auto, snapshot, datetime(2026, 8, 28, 12, 0))
        mock_write.assert_not_called()
        self.assertTrue(self.engine._last_eval[auto.id])


if __name__ == "__main__":
    unittest.main()
