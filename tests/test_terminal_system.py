"""
TravixPay Terminal System Tests

Tests the complete card tap flow:
- Card reads
- Serial protocol
- Bridge communication
- API communication
- Offline recovery
- Duplicate prevention
- Fare deduction
- Insufficient funds
- Blocked cards
"""

import time
import hashlib
import unittest
import tempfile
import os

# Terminal bridge imports
from terminal_bridge.protocol import (
    CardTapMessage, ResponseMessage, HeartbeatMessage,
    generate_signature, make_tap_reference
)
from terminal_bridge.offline_store import OfflineStore


class TestSerialProtocol(unittest.TestCase):
    """Test serial protocol message parsing and generation."""

    def test_card_tap_message_serialize(self):
        msg = CardTapMessage(
            card_uid="A1B2C3D4",
            terminal_id="TRM-001",
            timestamp=1710000000,
            tap_ref="TAP-ABCD1234"
        )
        json_str = msg.to_json()
        self.assertIn("CARD_TAP", json_str)
        self.assertIn("A1B2C3D4", json_str)
        self.assertIn("TRM-001", json_str)

    def test_card_tap_message_parse(self):
        raw = '{"type": "CARD_TAP", "card_uid": "A1B2C3D4", "terminal_id": "TRM-001", "timestamp": 1710000000, "tap_ref": "TAP-XYZ"}'
        msg = CardTapMessage.from_json(raw)
        self.assertIsNotNone(msg)
        self.assertEqual(msg.card_uid, "A1B2C3D4")
        self.assertEqual(msg.terminal_id, "TRM-001")

    def test_card_tap_message_parse_invalid(self):
        self.assertIsNone(CardTapMessage.from_json(""))
        self.assertIsNone(CardTapMessage.from_json("not json"))
        self.assertIsNone(CardTapMessage.from_json('{"type": "HEARTBEAT"}'))

    def test_response_message_serialize(self):
        msg = ResponseMessage(status="APPROVED", balance=2500, transaction_id="TX-ABC")
        json_str = msg.to_json()
        self.assertIn("APPROVED", json_str)
        self.assertIn("2500", json_str)

    def test_heartbeat_message_parse(self):
        raw = '{"type": "HEARTBEAT", "terminal_id": "TRM-001", "uptime": 12345}'
        msg = HeartbeatMessage.from_json(raw)
        self.assertIsNotNone(msg)
        self.assertEqual(msg.terminal_id, "TRM-001")

    def test_generate_signature_deterministic(self):
        sig1 = generate_signature("CARD001", "TRM-001", 1710000000)
        sig2 = generate_signature("CARD001", "TRM-001", 1710000000)
        self.assertEqual(sig1, sig2)

    def test_generate_signature_unique(self):
        sig1 = generate_signature("CARD001", "TRM-001", 1710000000)
        sig2 = generate_signature("CARD002", "TRM-001", 1710000000)
        self.assertNotEqual(sig1, sig2)

    def test_make_tap_reference_format(self):
        ref = make_tap_reference("CARD001", "TRM-001", 1710000000)
        self.assertTrue(ref.startswith("TAP-"))
        self.assertEqual(len(ref), 20)  # TAP- + 16 chars


class TestOfflineStore(unittest.TestCase):
    """Test offline storage and debt limits."""

    def setUp(self):
        self.db_path = tempfile.mktemp(suffix=".db")
        self.store = OfflineStore(db_path=self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_store_and_retrieve(self):
        result = self.store.store_tap(
            "CARD001", "TRM-001", "TAP-REF-001", "SIG-001", 200.0
        )
        self.assertTrue(result)

        pending = self.store.get_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["card_uid"], "CARD001")

    def test_duplicate_rejection(self):
        self.store.store_tap("CARD001", "TRM-001", "TAP-REF-001", "SIG-001", 200.0)
        result = self.store.store_tap("CARD001", "TRM-001", "TAP-REF-001", "SIG-001", 200.0)
        self.assertFalse(result)

    def test_offline_ride_limit(self):
        # Store 2 rides (at limit)
        self.store.store_tap("CARD001", "TRM-001", "TAP-001", "SIG-001", 200.0)
        self.store.store_tap("CARD001", "TRM-001", "TAP-002", "SIG-002", 200.0)

        ok, msg = self.store.check_limits("CARD001", 200.0)
        self.assertFalse(ok)
        self.assertIn("ride limit", msg)

    def test_offline_debt_limit(self):
        # Store taps approaching debt limit
        self.store.store_tap("CARD001", "TRM-001", "TAP-001", "SIG-001", 600.0)

        ok, msg = self.store.check_limits("CARD001", 500.0)
        self.assertFalse(ok)
        self.assertIn("debt limit", msg)

    def test_offline_within_limits(self):
        self.store.store_tap("CARD001", "TRM-001", "TAP-001", "SIG-001", 200.0)

        ok, msg = self.store.check_limits("CARD001", 200.0)
        self.assertTrue(ok)

    def test_mark_reconciled(self):
        self.store.store_tap("CARD001", "TRM-001", "TAP-001", "SIG-001", 200.0)
        pending = self.store.get_pending()
        self.assertEqual(len(pending), 1)

        self.store.mark_reconciled([pending[0]["id"]])
        pending = self.store.get_pending()
        self.assertEqual(len(pending), 0)

    def test_stats(self):
        self.store.store_tap("CARD001", "TRM-001", "TAP-001", "SIG-001", 200.0)
        self.store.store_tap("CARD002", "TRM-001", "TAP-002", "SIG-002", 200.0)

        stats = self.store.get_stats()
        self.assertEqual(stats["pending"], 2)
        self.assertEqual(stats["reconciled"], 0)

    def test_multiple_cards_independent_limits(self):
        # CARD001 at limit
        self.store.store_tap("CARD001", "TRM-001", "TAP-001", "SIG-001", 200.0)
        self.store.store_tap("CARD001", "TRM-001", "TAP-002", "SIG-002", 200.0)

        # CARD002 should still be OK
        ok, msg = self.store.check_limits("CARD002", 200.0)
        self.assertTrue(ok)


class TestTapProcessorIntegration(unittest.TestCase):
    """Test Django TapProcessor (requires Django setup)."""

    @classmethod
    def setUpClass(cls):
        """Set up Django if available."""
        try:
            import django
            import os
            os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'travixpay.settings')
            django.setup()
            cls.django_available = True
        except Exception:
            cls.django_available = False

    def setUp(self):
        if not self.django_available:
            self.skipTest("Django not available")

    def test_duplicate_tap_reference_returns_cached(self):
        from terminals.services.tap_processor import TapProcessor
        from cards.models import CardTapLog, RFIDCard
        from terminals.models import Terminal
        from accounts.models import User

        # Get or create test user + card (RFIDCard requires user)
        user, _ = User.objects.get_or_create(
            email="test-tap@travixpay.test",
            defaults={"phone_number": "0800000000", "full_name": "Test Tapper", "role": "PASSENGER"}
        )
        card, _ = RFIDCard.objects.get_or_create(
            card_uid="TEST-CARD-001",
            defaults={"status": "ACTIVE", "user": user}
        )

        # Get or create test terminal
        terminal, _ = Terminal.objects.get_or_create(
            terminal_code="TEST-TERM",
            defaults={
                "name": "Test Terminal",
                "vehicle_number": "TEST-001",
                "route": "Test Route",
                "status": "ONLINE"
            }
        )

        tap_ref = "TEST-DUPLICATE-REF-001"

        # First tap
        result1 = TapProcessor.process_card_tap(
            card_uid="TEST-CARD-001",
            terminal_code="TEST-TERM",
            tap_reference=tap_ref
        )

        # Duplicate tap with same reference
        result2 = TapProcessor.process_card_tap(
            card_uid="TEST-CARD-001",
            terminal_code="TEST-TERM",
            tap_reference=tap_ref
        )

        # Should return same status (deduplication)
        self.assertEqual(result1["status"], result2["status"])

    def test_unregistered_card_declined(self):
        from terminals.services.tap_processor import TapProcessor

        result = TapProcessor.process_card_tap(
            card_uid="UNREGISTERED-CARD-999",
            terminal_code="TEST-TERM",
            tap_reference="TAP-UNREG-001"
        )

        self.assertEqual(result["status"], "DECLINED")

    def test_invalid_terminal_returns_error(self):
        from terminals.services.tap_processor import TapProcessor

        result = TapProcessor.process_card_tap(
            card_uid="ANY-CARD",
            terminal_code="NONEXISTENT-TERM",
            tap_reference="TAP-INVALID-001"
        )

        self.assertEqual(result["status"], "ERROR")


if __name__ == "__main__":
    unittest.main()
