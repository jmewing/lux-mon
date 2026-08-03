"""Shared TCP utilities for passive and active TCP transports."""

import logging
import socket
from typing import Optional

logger = logging.getLogger("luxmon.comm.tcp")


def tcp_connect(host: str, port: int, timeout: float) -> Optional[socket.socket]:
    """Open a TCP connection to a dongle or gateway."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        logger.info("Connected to %s:%d", host, port)
        return sock
    except OSError as exc:
        logger.error("Failed to connect to %s:%d: %s", host, port, exc)
        return None


def safe_close(sock: Optional[socket.socket]) -> None:
    """Close a socket, ignoring errors."""
    if sock:
        try:
            sock.close()
        except OSError:
            pass
