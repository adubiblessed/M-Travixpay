"""
TravixPay Terminal Bridge - Main Entry Point

Receives card taps from Arduino via serial, processes them through
Django backend API, and sends responses back.

Handles offline mode: stores taps locally when backend is unreachable,
reconciles when connection is restored.
"""

import sys
import time
import signal
import logging

from terminal_bridge.config import (
    SERIAL_PORT, SERIAL_BAUD, BACKEND_URL, TERMINAL_ID,
    RECONCILE_INTERVAL, OFFLINE_DB_PATH
)
from terminal_bridge.serial_handler import SerialHandler
from terminal_bridge.api_client import ApiClient
from terminal_bridge.offline_store import OfflineStore
from terminal_bridge.protocol import CardTapMessage, generate_signature

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("terminal_bridge.log")
    ]
)
logger = logging.getLogger("terminal_bridge")


class TerminalBridge:
    def __init__(self, port=SERIAL_PORT, baudrate=SERIAL_BAUD,
                 backend_url=BACKEND_URL, terminal_id=TERMINAL_ID):
        self.serial = SerialHandler(port, baudrate)
        self.api = ApiClient(backend_url)
        self.offline = OfflineStore()
        self.terminal_id = terminal_id
        self.running = False
        self.last_reconcile = 0

    def start(self):
        """Main loop — reads taps from Arduino, processes, responds."""
        logger.info(f"=== TravixPay Terminal Bridge ===")
        logger.info(f"Terminal: {self.terminal_id}")
        logger.info(f"Backend: {self.api.base_url}")
        logger.info(f"Serial: {self.serial.port} @ {self.serial.baudrate}")

        if not self.serial.connect():
            logger.error("Failed to connect to serial port. Exiting.")
            sys.exit(1)

        self.running = True
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        while self.running:
            try:
                self._process_cycle()
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Bridge error: {e}")
                time.sleep(1)

        self.serial.disconnect()
        logger.info("Bridge stopped.")

    def _process_cycle(self):
        """One iteration: read serial, process, reconcile."""
        # 1. Read message from Arduino
        raw = self.serial.read_message()

        if raw and raw.get("type") == "CARD_TAP":
            self._handle_tap(raw)
        elif raw and raw.get("type") == "HEARTBEAT":
            logger.debug(f"Heartbeat from {raw.get('terminal_id')}")
        elif raw:
            logger.debug(f"Unknown message type: {raw.get('type')}")

        # 2. Periodic reconciliation
        now = time.time()
        if now - self.last_reconcile > RECONCILE_INTERVAL:
            self._reconcile()
            self.last_reconcile = now

        # 3. Small sleep to prevent CPU spin
        time.sleep(0.05)

    def _handle_tap(self, raw: dict):
        """Process a card tap from Arduino."""
        msg = CardTapMessage.from_json(
            __import__('json').dumps(raw)
        )
        if not msg:
            logger.warning(f"Invalid tap message: {raw}")
            self.serial.send_response("DECLINED", reason="INVALID_MESSAGE")
            return

        logger.info(f"Card tap: {msg.card_uid} at {msg.terminal_id}")

        # Generate deterministic tap reference
        tap_ref = getattr(msg, 'tap_ref', None) or generate_signature(
            msg.card_uid, msg.terminal_id, msg.timestamp
        )

        # Try online first — fare resolved by backend from destination FareRule
        response = self.api.send_tap(
            card_uid=msg.card_uid,
            terminal_id=msg.terminal_id,
            tap_reference=tap_ref,
            fare_amount=None
        )

        if response is not None:
            # Online response
            status = response.get("status", "ERROR")
            reason = response.get("reason", "")
            balance = int(float(response.get("balance", 0)))
            tx_id = response.get("reference", "")

            self.serial.send_response(
                status=status,
                reason=reason,
                transaction_id=tx_id,
                balance=balance
            )
            logger.info(f"Online response: {status} | {reason}")
            return

        # Offline path
        # Cannot resolve fare from backend (offline) — store with 0 fare
        # Backend will resolve actual fare during reconciliation
        logger.warning("Backend offline — storing tap for reconciliation")
        offline_fare = 0.00  # Fare resolved by backend on reconcile

        # Check offline limits (use conservative estimate)
        ok, limit_msg = self.offline.check_limits(msg.card_uid, 1000.00)
        if not ok:
            self.serial.send_response("DECLINED", reason=limit_msg)
            logger.warning(f"Offline declined: {limit_msg}")
            return

        sig = generate_signature(msg.card_uid, msg.terminal_id, msg.timestamp)
        stored = self.offline.store_tap(
            card_uid=msg.card_uid,
            terminal_id=msg.terminal_id,
            tap_reference=tap_ref,
            signature=sig,
            fare=offline_fare
        )

        if stored:
            self.serial.send_response("APPROVED", reason="OFFLINE_APPROVED")
            logger.info("Offline tap approved and stored")
        else:
            self.serial.send_response("DECLINED", reason="DUPLICATE_TAP")

    def _reconcile(self):
        """Sync offline taps to backend."""
        pending = self.offline.get_pending(limit=10)
        if not pending:
            return

        logger.info(f"Reconciling {len(pending)} offline taps...")

        if not self.api.check_health():
            logger.warning("Backend still offline — skipping reconciliation")
            return

        reconciled_ids = []
        for tap in pending:
            response = self.api.send_tap(
                card_uid=tap["card_uid"],
                terminal_id=tap["terminal_id"],
                tap_reference=tap["tap_reference"],
                fare_amount=float(tap["fare_amount"])
            )

            if response is not None:
                reconciled_ids.append(tap["id"])
            else:
                logger.warning(f"Reconciliation failed for {tap['tap_reference']}")
                break  # Backend went offline again

        if reconciled_ids:
            self.offline.mark_reconciled(reconciled_ids)
            logger.info(f"Reconciled {len(reconciled_ids)} taps")

    def _handle_signal(self, signum, frame):
        logger.info(f"Signal {signum} received, shutting down...")
        self.running = False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="TravixPay Terminal Bridge")
    parser.add_argument("--port", default=SERIAL_PORT, help="Serial port")
    parser.add_argument("--baud", type=int, default=SERIAL_BAUD, help="Baud rate")
    parser.add_argument("--backend", default=BACKEND_URL, help="Backend URL")
    parser.add_argument("--terminal", default=TERMINAL_ID, help="Terminal ID")
    args = parser.parse_args()

    bridge = TerminalBridge(
        port=args.port,
        baudrate=args.baud,
        backend_url=args.backend,
        terminal_id=args.terminal
    )
    bridge.start()


if __name__ == "__main__":
    main()
