"""
TravixPay Terminal Bridge - Serial Protocol

Protocol format: JSON + newline delimited

Card Tap Request:
  {"type": "CARD_TAP", "card_uid": "A1B2C3D4", "terminal_id": "TRM-001", "timestamp": 1710000000, "tap_ref": "TAP-A1B2C3D4-1710000000"}

Response:
  {"type": "RESPONSE", "status": "APPROVED", "transaction_id": "...", "balance": 2500}
  {"type": "RESPONSE", "status": "DECLINED", "reason": "INSUFFICIENT_FUNDS"}

Heartbeat:
  {"type": "HEARTBEAT", "terminal_id": "TRM-001", "uptime": 12345}
"""

import json
import hashlib
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class CardTapMessage:
    type: str = "CARD_TAP"
    card_uid: str = ""
    terminal_id: str = ""
    timestamp: int = 0
    tap_ref: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str) -> Optional['CardTapMessage']:
        try:
            data = json.loads(raw)
            if data.get("type") != "CARD_TAP":
                return None
            return CardTapMessage(
                type="CARD_TAP",
                card_uid=data.get("card_uid", ""),
                terminal_id=data.get("terminal_id", ""),
                timestamp=data.get("timestamp", 0),
                tap_ref=data.get("tap_ref", ""),
            )
        except (json.JSONDecodeError, KeyError):
            return None


@dataclass
class ResponseMessage:
    type: str = "RESPONSE"
    status: str = ""
    reason: str = ""
    transaction_id: str = ""
    balance: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class HeartbeatMessage:
    type: str = "HEARTBEAT"
    terminal_id: str = ""
    uptime: int = 0

    @staticmethod
    def from_json(raw: str) -> Optional['HeartbeatMessage']:
        try:
            data = json.loads(raw)
            if data.get("type") != "HEARTBEAT":
                return None
            return HeartbeatMessage(
                terminal_id=data.get("terminal_id", ""),
                uptime=data.get("uptime", 0),
            )
        except (json.JSONDecodeError, KeyError):
            return None


def generate_signature(card_uid: str, terminal_id: str, timestamp: int) -> str:
    """Generate SHA256 signature for deduplication."""
    raw = f"{card_uid}:{terminal_id}:{timestamp}"
    return hashlib.sha256(raw.encode()).hexdigest()


def make_tap_reference(card_uid: str, terminal_id: str, timestamp: int) -> str:
    """Generate unique tap reference."""
    sig = generate_signature(card_uid, terminal_id, timestamp)
    return f"TAP-{sig[:16]}"
