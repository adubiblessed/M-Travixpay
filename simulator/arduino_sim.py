"""
TravixPay Arduino Simulator

Emulates an Arduino + MFRC522 terminal without physical hardware.
Sends card taps directly to Django backend API.

Usage:
    python -m simulator.arduino_sim --terminal TRM-001 --backend http://127.0.0.1:8000

Commands:
    tap CARD001         - Simulate card tap
    tap CARD001 500     - Simulate card tap with custom fare
    block CARD002       - Mark card as blocked (simulates RFID rejection)
    offline             - Go offline (stop sending to backend)
    online              - Go back online
    status              - Show terminal status
    stats               - Show offline store stats
    reconcile           - Force reconciliation of offline taps
    help                - Show commands
    quit                - Exit
"""

import sys
import os
import time
import uuid
import hashlib
import requests
import sqlite3
import readline  # noqa: F401 — enables arrow keys in input()

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from terminal_bridge.api_client import ApiClient
from terminal_bridge.offline_store import OfflineStore
from terminal_bridge.protocol import generate_signature


class ArduinoSimulator:
    def __init__(self, terminal_id: str = "TRM-001", backend_url: str = "http://127.0.0.1:8000"):
        self.terminal_id = terminal_id
        self.api = ApiClient(backend_url)
        self.offline_store = OfflineStore(db_path="simulator_offline.db")
        self.is_online = True
        self.blocked_cards = set()
        self.tap_count = 0
        self.approved_count = 0
        self.declined_count = 0

    def run(self):
        print(f"\n{'='*50}")
        print(f"  TravixPay Arduino Simulator")
        print(f"  Terminal: {self.terminal_id}")
        print(f"  Backend: {self.api.base_url}")
        print(f"")
        print(f"  IMPORTANT: Set terminal to your driver destination code!")
        print(f"  Example: set terminal TRM-CAB57D82")
        print(f"  Type 'help' for all commands")
        print(f"{'='*50}\n")

        while True:
            try:
                cmd = input(f"[{self.terminal_id}] > ").strip()
                if not cmd:
                    continue
                self._process_command(cmd)
            except (EOFError, KeyboardInterrupt):
                print("\nExiting simulator.")
                break

    def _process_command(self, cmd: str):
        parts = cmd.split()
        action = parts[0].lower()

        if action == "tap":
            card_uid = parts[1] if len(parts) > 1 else "CARD001"
            self._simulate_tap(card_uid)

        elif action == "set":
            # set terminal TRM-XXXX
            if len(parts) >= 3 and parts[1].lower() == "terminal":
                old = self.terminal_id
                self.terminal_id = parts[2]
                print(f"  Terminal changed: {old} → {self.terminal_id}")
            else:
                print("  Usage: set terminal TRM-XXXX")

        elif action == "block":
            card_uid = parts[1] if len(parts) > 1 else "CARD001"
            self.blocked_cards.add(card_uid)
            print(f"  Card {card_uid} marked as blocked")

        elif action == "unblock":
            card_uid = parts[1] if len(parts) > 1 else "CARD001"
            self.blocked_cards.discard(card_uid)
            print(f"  Card {card_uid} unblocked")

        elif action == "offline":
            self.is_online = False
            self.api.is_online = False
            print("  Switched to OFFLINE mode")

        elif action == "online":
            self.is_online = True
            self.api.is_online = True
            print("  Switched to ONLINE mode")

        elif action == "status":
            self._show_status()

        elif action == "stats":
            self._show_stats()

        elif action == "reconcile":
            self._force_reconcile()

        elif action == "help":
            self._show_help()

        elif action in ("quit", "exit", "q"):
            raise SystemExit(0)

        else:
            print(f"  Unknown command: {action}. Type 'help'.")

    def _simulate_tap(self, card_uid: str):
        self.tap_count += 1
        timestamp = int(time.time())
        tap_ref = f"TAP-{hashlib.sha256(f'{card_uid}:{self.terminal_id}:{timestamp}'.encode()).hexdigest()[:16]}"

        print(f"\n  [TAP] Card: {card_uid} | Terminal: {self.terminal_id} | Ref: {tap_ref}")

        # Check blocked
        if card_uid in self.blocked_cards:
            print(f"  [RED LED] [BUZZER: LONG] DECLINED — Card is BLOCKED")
            self.declined_count += 1
            return

        # Try online — fare resolved by backend from destination FareRule
        if self.is_online:
            response = self.api.send_tap(card_uid, self.terminal_id, tap_ref)

            if response is None:
                # Backend unreachable — go offline
                print(f"  [WARN] Backend unreachable")
                self._handle_offline_tap(card_uid, tap_ref)
                return

            status = response.get("status", "ERROR")
            reason = response.get("reason", "")
            balance = response.get("balance", 0)

            if status == "APPROVED":
                print(f"  [GREEN LED] [BUZZER: SHORT] APPROVED")
                print(f"  Remaining balance: ₦{balance}")
                self.approved_count += 1
            else:
                print(f"  [RED LED] [BUZZER: LONG] DECLINED — {reason}")
                self.declined_count += 1
        else:
            self._handle_offline_tap(card_uid, tap_ref)

    def _handle_offline_tap(self, card_uid: str, tap_ref: str):
        # Offline: use conservative limit check (fare resolved by backend on reconcile)
        ok, msg = self.offline_store.check_limits(card_uid, 1000.00)
        if not ok:
            print(f"  [RED LED] [BUZZER: LONG] DECLINED — {msg}")
            self.declined_count += 1
            return

        sig = generate_signature(card_uid, self.terminal_id, int(time.time()))
        stored = self.offline_store.store_tap(
            card_uid, self.terminal_id, tap_ref, sig, 0.00  # Fare resolved on reconcile
        )
        if stored:
            print(f"  [GREEN LED] [BUZZER: SHORT] APPROVED (OFFLINE)")
            self.approved_count += 1
        else:
            print(f"  [RED LED] DECLINED — Duplicate tap")
            self.declined_count += 1

    def _force_reconcile(self):
        pending = self.offline_store.get_pending()
        if not pending:
            print("  No pending offline taps to reconcile")
            return

        if not self.is_online:
            print("  Cannot reconcile — still offline")
            return

        print(f"  Reconciling {len(pending)} taps...")
        ids = []
        for tap in pending:
            # Backend resolves actual fare from destination FareRule
            resp = self.api.send_tap(
                tap["card_uid"], tap["terminal_id"],
                tap["tap_reference"]
            )
            if resp:
                ids.append(tap["id"])
            else:
                print(f"  Failed to sync {tap['tap_reference']}")
                break

        if ids:
            self.offline_store.mark_reconciled(ids)
            print(f"  Reconciled {len(ids)} taps")

    def _show_status(self):
        print(f"\n  Terminal: {self.terminal_id}")
        print(f"  Mode: {'ONLINE' if self.is_online else 'OFFLINE'}")
        print(f"  Taps: {self.tap_count} ({self.approved_count} approved, {self.declined_count} declined)")
        print(f"  Blocked cards: {self.blocked_cards or 'none'}")
        print()

    def _show_stats(self):
        stats = self.offline_store.get_stats()
        print(f"\n  Offline Store:")
        print(f"    Pending: {stats['pending']}")
        print(f"    Reconciled: {stats['reconciled']}")
        print(f"    Failed: {stats['failed']}")
        print()

    def _show_help(self):
        print("""
  Commands:
    tap CARD001           Simulate card tap (fare from destination FareRule)
    set terminal TRM-XX   Change terminal code (use driver's destination code)
    block CARD001         Block a card (simulates RFID block)
    unblock CARD001       Unblock a card
    offline               Switch to offline mode
    online                Switch to online mode
    status                Show terminal status
    stats                 Show offline store stats
    reconcile             Force sync offline taps to backend
    help                  Show this help
    quit                  Exit simulator
""")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="TravixPay Arduino Simulator")
    parser.add_argument("--terminal", default="TRM-001", help="Terminal ID")
    parser.add_argument("--backend", default="http://127.0.0.1:8000", help="Backend URL")
    args = parser.parse_args()

    sim = ArduinoSimulator(terminal_id=args.terminal, backend_url=args.backend)
    sim.run()


if __name__ == "__main__":
    main()
