"""Tests for WriteMultipleRegisters (0x10) support."""
import struct

from collector.protocol import (
    build_write_multi_request,
    build_write_request,
    crc16_modbus,
    find_frames,
    parse_frame,
    _serial_to_bytes,
    MODBUS_WRITE_MULTI,
    MODBUS_WRITE_SINGLE,
    PREFIX,
    TCP_FUNC_TRANSLATED_DATA,
)


DATALOG_SERIAL = "BJ12345"
INVERTER_SERIAL = "INV12345"


def _wrap_modbus_frame(modbus_frame: bytes, datalog_serial: str = DATALOG_SERIAL) -> bytes:
    """Wrap a raw Modbus data frame in a LuxPower TranslatedData outer packet."""
    outer = bytearray()
    outer += PREFIX
    outer += struct.pack("<H", 1)  # protocol
    total = 18 + 2 + len(modbus_frame)
    outer += struct.pack("<H", total - 6)  # frame length
    outer.append(0x01)
    outer.append(TCP_FUNC_TRANSLATED_DATA)
    outer += _serial_to_bytes(datalog_serial)
    outer += struct.pack("<H", len(modbus_frame))  # data length
    outer += modbus_frame
    return bytes(outer)


def _build_modbus_echo(function: int, start_register: int, count_or_value: int,
                       inverter_serial: str = INVERTER_SERIAL) -> bytes:
    """Build a raw Modbus data frame echo for a write function."""
    frame = bytearray()
    frame.append(0x00)
    frame.append(function)
    frame += _serial_to_bytes(inverter_serial)
    frame += struct.pack("<H", start_register)
    frame += struct.pack("<H", count_or_value)
    frame += struct.pack("<H", crc16_modbus(bytes(frame)))
    return bytes(frame)


def _build_modbus_error(function: int, error_code: int) -> bytes:
    """Build a raw Modbus error response frame."""
    frame = bytearray()
    frame.append(0x00)
    frame.append(function + 0x80)
    frame.append(error_code)
    frame += struct.pack("<H", crc16_modbus(bytes(frame)))
    return bytes(frame)


def test_build_write_multi_request_layout():
    pkt = build_write_multi_request(DATALOG_SERIAL, INVERTER_SERIAL, 500, [100, 200])
    # Outer header
    assert pkt[0:2] == PREFIX
    assert struct.unpack_from("<H", pkt, 2)[0] == 1  # protocol
    assert struct.unpack_from("<H", pkt, 4)[0] == len(pkt) - 6  # frame length
    assert pkt[7] == TCP_FUNC_TRANSLATED_DATA
    assert pkt[8:18] == _serial_to_bytes(DATALOG_SERIAL)
    # Data length prefix
    data_len = struct.unpack_from("<H", pkt, 18)[0]
    assert data_len == len(pkt) - 20
    # Modbus frame
    assert pkt[20] == 0x00  # address
    assert pkt[21] == MODBUS_WRITE_MULTI  # function 0x10
    assert pkt[22:32] == _serial_to_bytes(INVERTER_SERIAL)
    assert struct.unpack_from("<H", pkt, 32)[0] == 500  # start register
    assert struct.unpack_from("<H", pkt, 34)[0] == 2  # count
    assert pkt[36] == 4  # byte count (2 registers × 2 bytes)
    assert struct.unpack_from("<H", pkt, 37)[0] == 100
    assert struct.unpack_from("<H", pkt, 39)[0] == 200
    # CRC over [20:41]
    crc = crc16_modbus(bytes(pkt[20:41]))
    assert struct.unpack_from("<H", pkt, 41)[0] == crc


def test_build_write_multi_request_rejects_empty():
    import pytest
    with pytest.raises(ValueError):
        build_write_multi_request(DATALOG_SERIAL, INVERTER_SERIAL, 500, [])


def test_build_write_multi_request_rejects_too_many():
    import pytest
    with pytest.raises(ValueError):
        build_write_multi_request(DATALOG_SERIAL, INVERTER_SERIAL, 500, [0] * 124)


def test_build_write_multi_request_rejects_out_of_range():
    import pytest
    with pytest.raises(ValueError):
        build_write_multi_request(DATALOG_SERIAL, INVERTER_SERIAL, 500, [0x10000])


def test_parse_write_multi_echo():
    echo = _build_modbus_echo(MODBUS_WRITE_MULTI, 500, 2)
    pkt = _wrap_modbus_frame(echo)
    frames = find_frames(pkt)
    assert len(frames) == 1
    f = frames[0]
    assert not f.is_error
    assert f.device_function == MODBUS_WRITE_MULTI
    assert f.register == 500
    assert f.write_count == 2


def test_parse_write_multi_error():
    err = _build_modbus_error(MODBUS_WRITE_MULTI, 0x02)
    pkt = _wrap_modbus_frame(err)
    frames = find_frames(pkt)
    assert len(frames) == 1
    f = frames[0]
    assert f.is_error
    assert f.error_code == 0x02


def test_parse_write_single_echo():
    # Regression: single-register write (0x06) echo still parses correctly.
    echo = _build_modbus_echo(MODBUS_WRITE_SINGLE, 60, 50)
    pkt = _wrap_modbus_frame(echo)
    frames = find_frames(pkt)
    assert len(frames) == 1
    f = frames[0]
    assert not f.is_error
    assert f.device_function == MODBUS_WRITE_SINGLE
    assert f.register == 60
    assert f.values == [50]


def test_parse_write_single_error():
    err = _build_modbus_error(MODBUS_WRITE_SINGLE, 0x03)
    pkt = _wrap_modbus_frame(err)
    frames = find_frames(pkt)
    assert len(frames) == 1
    f = frames[0]
    assert f.is_error
    assert f.error_code == 0x03


def test_write_holding_registers_transport():
    """End-to-end: _write_holding_registers sends 0x10 and verifies the echo."""
    from unittest.mock import MagicMock, patch
    from collector.automation import _write_holding_registers

    echo = _build_modbus_echo(MODBUS_WRITE_MULTI, 500, 2)
    pkt = _wrap_modbus_frame(echo)

    fake_sock = MagicMock()
    fake_sock.recv.side_effect = [pkt, b""]

    with patch("collector.automation.socket.create_connection", return_value=fake_sock):
        ok, msg = _write_holding_registers(
            "192.168.1.100", 8000, DATALOG_SERIAL, INVERTER_SERIAL, 500, [100, 200]
        )

    assert ok is True
    assert "500" in msg
    # The request sent must be a 0x10 multi-write with the right start/count.
    sent = fake_sock.sendall.call_args[0][0]
    assert sent[21] == MODBUS_WRITE_MULTI
    assert struct.unpack_from("<H", sent, 32)[0] == 500
    assert struct.unpack_from("<H", sent, 34)[0] == 2


def test_write_holding_registers_error():
    """A Modbus error response surfaces as ok=False with the error code."""
    from unittest.mock import MagicMock, patch
    from collector.automation import _write_holding_registers

    err = _build_modbus_error(MODBUS_WRITE_MULTI, 0x02)
    pkt = _wrap_modbus_frame(err)

    fake_sock = MagicMock()
    fake_sock.recv.side_effect = [pkt, b""]

    with patch("collector.automation.socket.create_connection", return_value=fake_sock):
        ok, msg = _write_holding_registers(
            "192.168.1.100", 8000, DATALOG_SERIAL, INVERTER_SERIAL, 500, [100, 200]
        )

    assert ok is False
    assert "2" in msg  # error code 0x02
