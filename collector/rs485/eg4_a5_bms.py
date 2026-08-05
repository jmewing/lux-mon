"""EG4 battery BMS driver via the proprietary A5/5A serial protocol.

This driver decodes the broadcast frames emitted by EG4 rack/wall-mount
LiFePO4 batteries on their "PC ready" / RS-485 / display port.  The frame
format is:

    a5 5a <length> <command> <subcommand> <payload...>

where <length> is the number of bytes following the length byte
(i.e. command + subcommand + payload).  Frames we care about:

    command=0x82, subcommand=0x10  -> status frame
    command=0x82, subcommand=0x11  -> cell voltages frame

The BMS appears to broadcast these continuously when a master/display is
connected, so the driver can operate in passive listen mode.  It also sends a
minimal poll probe to wake a quiet bus when needed.

Reference: reverse-engineered from live captures on an EG4 200Ah battery with
a Silicon Labs CP2102 USB-to-UART bridge at 115200 8N1.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

from . import Rs485Device, Rs485DeviceConfig

logger = logging.getLogger("luxmon.rs485.eg4_a5_bms")

try:
    import serial
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "pyserial is required for RS-485 support. "
        "Install it with: pip install pyserial>=3.5"
    ) from exc


MAGIC = bytes([0xA5, 0x5A])

# Command/subcommand pairs
CMD_STATUS = (0x82, 0x10)
CMD_CELLS = (0x82, 0x11)
CMD_BALANCING = (0x82, 0x13)

# Minimal poll probes used to wake a quiet BMS.  Format:
# a5 5a <len=5> 82 <sub=0x21> <index> 00 00
_POLL_STATUS = bytes([0xA5, 0x5A, 0x05, 0x82, 0x21, 0x13, 0x00, 0x00])
_POLL_CELLS = bytes([0xA5, 0x5A, 0x05, 0x82, 0x21, 0x23, 0x00, 0x00])


def _split_frames(data: bytes) -> list[bytes]:
    """Split a byte stream into A5/5A length-delimited frames."""
    frames: list[bytes] = []
    i = 0
    n = len(data)
    while i < n - 2:
        if data[i : i + 2] == MAGIC:
            length = data[i + 2]
            end = i + 3 + length
            if end <= n:
                frames.append(data[i:end])
                i = end
                continue
        i += 1
    return frames


def _u16_be(data: bytes, offset: int) -> int:
    return (data[offset] << 8) | data[offset + 1]


def _s16_be(data: bytes, offset: int) -> int:
    val = _u16_be(data, offset)
    if val >= 0x8000:
        val -= 0x10000
    return val


class Eg4A5BmsDevice(Rs485Device):
    """EG4 battery BMS using the A5/5A proprietary protocol."""

    name = "eg4_a5_bms"
    label = "EG4 battery BMS (A5/5A protocol)"

    def __init__(self, config: Rs485DeviceConfig):
        super().__init__(config)
        self._port: Optional[serial.Serial] = None
        self._latest: Dict[str, Any] = {}

    def _open(self) -> serial.Serial:
        if self._port is not None and self._port.is_open:
            return self._port
        self._port = serial.Serial(
            port=self.cfg.port,
            baudrate=self.cfg.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.cfg.timeout,
            write_timeout=self.cfg.timeout,
        )
        logger.debug("Opened %s at %d baud", self.cfg.port, self.cfg.baudrate)
        return self._port

    def close(self) -> None:
        if self._port is not None and self._port.is_open:
            try:
                self._port.close()
            except Exception:
                logger.exception("Error closing serial port")
        self._port = None

    def read(self) -> Dict[str, Dict[str, Any]]:
        port = self._open()
        port.reset_input_buffer()

        # Send a couple of poll probes to make sure a quiet BMS starts talking.
        try:
            port.write(_POLL_STATUS)
            time.sleep(0.05)
            port.write(_POLL_CELLS)
        except Exception:
            logger.exception("Failed to write poll probe")

        # Listen for one timeout window; the BMS broadcasts continuously.
        deadline = time.time() + max(self.cfg.timeout, 0.5)
        chunks: list[bytes] = []
        while time.time() < deadline:
            chunk = port.read(2048)
            if chunk:
                chunks.append(chunk)
            else:
                # No more buffered data right now; if we already parsed
                # something useful we can stop early.
                if self._latest:
                    break

        if not chunks:
            return {}

        raw = b"".join(chunks)
        frames = _split_frames(raw)
        if not frames:
            logger.debug("No A5/5A frames found in %d bytes", len(raw))
            return {}

        for frame in frames:
            if len(frame) < 5:
                continue
            cmd, sub = frame[3], frame[4]
            payload = frame[5:]

            if (cmd, sub) == CMD_STATUS:
                parsed = self._parse_status(payload)
                if parsed:
                    self._latest.update(parsed)
            elif (cmd, sub) == CMD_CELLS:
                parsed = self._parse_cells(payload)
                if parsed:
                    self._latest.update(parsed)

        if not self._latest:
            return {}

        result = self._build_result()
        logger.info(
            "EG4 A5 BMS: %.2f V, %.2f A, %.1f%% SOC, %d cells",
            result.get("voltage", {}).get("value", 0.0),
            result.get("current", {}).get("value", 0.0),
            result.get("soc", {}).get("value", 0.0),
            int(result.get("cell_count", {}).get("value", 0.0)),
        )
        return result

    def _parse_status(self, payload: bytes) -> Optional[Dict[str, Any]]:
        """Parse the 0x82 0x10 status payload.

        Payload layout (35 bytes observed, big-endian 16-bit values after an
        initial zero/status byte):

            offset 0   : unknown status byte (always 0x00 in captures)
            offset 1-2 : pack voltage * 0.01 V
            offset 3-4 : current * 0.1 A, signed (negative = discharge)
            offset 5-6 : temperature (likely MOSFET/PCB, °C)
            offset 7-8 : unknown / 0
            offset 9-10: unknown (possibly SOC or SOH; varies 85-86 in capture)
            offset 11-12: unknown (35 in capture)
            offset 13-14: unknown (36 in capture)
            offset 15-16: unknown / 0
            offset 17-18: average cell voltage, mV
            offset 19-34: status/protection bit fields (still being mapped)
        """
        if len(payload) < 19:
            return None

        result: Dict[str, Any] = {}
        result["voltage_raw"] = _u16_be(payload, 1)
        result["current_raw"] = _s16_be(payload, 3)
        result["voltage"] = round(result["voltage_raw"] * 0.01, 2)
        result["current"] = round(result["current_raw"] * 0.1, 2)
        result["power"] = round(result["voltage"] * result["current"], 2)

        # Temperature candidate at offset 5
        result["temperature_pcb_raw"] = _u16_be(payload, 5)
        result["temperature_pcb"] = float(result["temperature_pcb_raw"])

        # Other candidate numeric fields (exported raw for verification)
        result["field_7_8"] = _u16_be(payload, 7)
        result["field_9_10"] = _u16_be(payload, 9)
        result["field_11_12"] = _u16_be(payload, 11)
        result["field_13_14"] = _u16_be(payload, 13)
        result["field_15_16"] = _u16_be(payload, 15)
        result["avg_cell_voltage"] = _u16_be(payload, 17)

        # Status/protection bits after the average cell voltage
        if len(payload) >= 21:
            result["status_word"] = _u16_be(payload, 19)
        if len(payload) >= 23:
            result["protection_word"] = _u16_be(payload, 21)
        if len(payload) >= 25:
            result["error_word"] = _u16_be(payload, 23)

        return result

    def _parse_cells(self, payload: bytes) -> Optional[Dict[str, Any]]:
        """Parse the 0x82 0x11 cell-voltage payload.

        Layout (57 bytes observed):
            offset 0   : unknown / 0x00
            offset 1-32: 16 cell voltages in mV (big-endian 16-bit each)
            offset 33-56: reserved / zero in captures
        """
        if len(payload) < 33:
            return None

        cell_voltages: list[int] = []
        min_v = 10000
        max_v = 0
        min_idx = 0
        max_idx = 0
        sum_v = 0

        for i in range(16):
            mv = _u16_be(payload, 1 + i * 2)
            cell_voltages.append(mv)
            sum_v += mv
            if mv < min_v:
                min_v = mv
                min_idx = i + 1
            if mv > max_v:
                max_v = mv
                max_idx = i + 1

        result: Dict[str, Any] = {}
        result["cell_count"] = 16
        for i, mv in enumerate(cell_voltages, start=1):
            result[f"cell_{i}_voltage"] = mv * 0.001
        result["cell_min_voltage"] = min_v * 0.001
        result["cell_max_voltage"] = max_v * 0.001
        result["cell_avg_voltage"] = round(sum_v / 16.0 * 0.001, 3)
        result["cell_delta_voltage"] = round((max_v - min_v) * 0.001, 3)
        result["cell_min_index"] = float(min_idx)
        result["cell_max_index"] = float(max_idx)
        return result

    def _build_result(self) -> Dict[str, Dict[str, Any]]:
        """Convert the internal flat dictionary to lux-mon snapshot shape."""
        out: Dict[str, Dict[str, Any]] = {}

        mapping: Dict[str, Tuple[str, str]] = {
            "voltage": ("voltage", "V"),
            "current": ("current", "A"),
            "power": ("power", "W"),
            "temperature_pcb": ("temperature_pcb", "°C"),
            "avg_cell_voltage": ("avg_cell_voltage", "mV"),
            "cell_count": ("cell_count", ""),
            "cell_min_voltage": ("cell_min_voltage", "V"),
            "cell_max_voltage": ("cell_max_voltage", "V"),
            "cell_avg_voltage": ("cell_avg_voltage", "V"),
            "cell_delta_voltage": ("cell_delta_voltage", "V"),
            "cell_min_index": ("cell_min_index", ""),
            "cell_max_index": ("cell_max_index", ""),
            "status_word": ("status_word", ""),
            "protection_word": ("protection_word", ""),
            "error_word": ("error_word", ""),
        }

        for key, (out_key, unit) in mapping.items():
            if key in self._latest:
                out[out_key] = {"value": float(self._latest[key]), "unit": unit}

        for i in range(1, 17):
            key = f"cell_{i}_voltage"
            if key in self._latest:
                out[key] = {"value": float(self._latest[key]), "unit": "V"}

        # Expose raw status/protection words as booleans for easy dashboards.
        if "status_word" in self._latest:
            word = int(self._latest["status_word"])
            out["charging"] = {"value": float(bool(word & 0x0001)), "unit": ""}
            out["discharging"] = {"value": float(bool(word & 0x0002)), "unit": ""}
            out["heating"] = {"value": float(bool(word & 0x8000)), "unit": ""}

        if "protection_word" in self._latest:
            word = int(self._latest["protection_word"])
            out["protection_active"] = {"value": float(bool(word)), "unit": ""}

        return out
