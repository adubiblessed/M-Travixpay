"""
TravixPay Terminal Bridge - Serial Handler

Handles communication with Arduino via USB serial.
Reads newline-delimited JSON messages, parses them, and sends responses.
"""

import json
import time
import logging
from typing import Optional, Callable

from terminal_bridge.protocol import CardTapMessage, ResponseMessage, HeartbeatMessage

logger = logging.getLogger("terminal_bridge")

try:
    import serial
except ImportError:
    serial = None
    logger.warning("pyserial not installed — serial handler in emulation mode")


class SerialHandler:
    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 1.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.connection = None
        self.is_connected = False

    def connect(self) -> bool:
        """Connect to serial port."""
        if serial is None:
            logger.warning("pyserial not available — emulation mode")
            self.is_connected = True
            return True

        try:
            if self.connection and self.connection.is_open:
                self.connection.close()
            self.connection = serial.Serial(
                self.port, self.baudrate, timeout=self.timeout
            )
            self.is_connected = True
            logger.info(f"Connected to {self.port} at {self.baudrate} baud")
            return True
        except Exception as e:
            logger.error(f"Serial connect failed: {e}")
            self.is_connected = False
            return False

    def disconnect(self):
        if self.connection and self.connection.is_open:
            self.connection.close()
        self.is_connected = False

    def read_message(self) -> Optional[dict]:
        """Read one newline-delimited JSON message from Arduino."""
        if serial is None:
            return None  # Emulation mode — no real serial

        if not self.connection or not self.connection.is_open:
            return None

        try:
            if self.connection.in_waiting == 0:
                return None

            line = self.connection.readline().decode('utf-8').strip()
            if not line:
                return None

            data = json.loads(line)
            logger.debug(f"Received: {data}")
            return data

        except json.JSONDecodeError:
            logger.warning(f"Malformed JSON from Arduino: {line[:100]}")
            return None
        except Exception as e:
            logger.error(f"Serial read error: {e}")
            self.is_connected = False
            return None

    def send_response(self, status: str, reason: str = "",
                      transaction_id: str = "", balance: int = 0):
        """Send response back to Arduino."""
        msg = ResponseMessage(
            status=status,
            reason=reason,
            transaction_id=transaction_id,
            balance=balance
        )
        self._send(msg.to_json())

    def _send(self, data: str):
        """Send raw string over serial."""
        if serial is None:
            logger.info(f"[EMULATED SEND] {data}")
            return

        if not self.connection or not self.connection.is_open:
            logger.warning("Cannot send — serial not connected")
            return

        try:
            self.connection.write((data + "\n").encode('utf-8'))
            self.connection.flush()
            logger.debug(f"Sent: {data}")
        except Exception as e:
            logger.error(f"Serial send error: {e}")
            self.is_connected = False
