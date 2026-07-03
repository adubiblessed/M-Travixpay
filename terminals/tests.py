from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from decimal import Decimal
import uuid

from wallets.models import Wallet, WalletLedger
from cards.models import RFIDCard, CardTapLog
from terminals.models import Terminal, FareRule, FareTransaction
from terminals.services.tap_processor import TapProcessor

User = get_user_model()

@override_settings(CACHES={
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'terminals-tests-cache',
    }
})
class TerminalTapTests(TestCase):
    def setUp(self):
        self.passenger = User.objects.create_user(
            email="passenger@travixpay.com",
            phone_number="+2348011111111",
            full_name="Passenger Test User",
            password="securepassword123"
        )
        self.wallet = Wallet.objects.create(
            user=self.passenger,
            status='ACTIVE',
            currency='NGN'
        )
        # Fund wallet with 1000 NGN
        WalletLedger.objects.create(
            wallet=self.wallet,
            reference="CR-INITIAL",
            entry_type='CREDIT',
            amount=Decimal('1000.00'),
            description="Initial Funding",
            source="TEST",
            source_id="1"
        )
        
        self.card = RFIDCard.objects.create(
            user=self.passenger,
            card_uid="RFID-12345",
            status='ACTIVE'
        )
        
        self.terminal = Terminal.objects.create(
            name="Main Bus Terminal 1",
            terminal_code="TERM-001",
            vehicle_number="LAG-123-AA",
            route="Lekki-Ajah",
            status="ONLINE"
        )
        
        self.fare_rule = FareRule.objects.create(
            route_name="Lekki-Ajah",
            amount=Decimal('350.00'),
            is_active=True
        )

    def test_successful_tap(self):
        tap_ref = "tap-ref-1"
        result = TapProcessor.process_card_tap(
            card_uid="RFID-12345",
            terminal_code="TERM-001",
            tap_reference=tap_ref
        )
        
        self.assertEqual(result["status"], "APPROVED")
        self.assertEqual(result["fare"], Decimal('350.00'))
        self.assertEqual(result["balance"], Decimal('650.00'))

        # Verify ledger entries and fare transaction
        self.assertTrue(WalletLedger.objects.filter(reference=f"DR-{tap_ref}").exists())
        self.assertTrue(FareTransaction.objects.filter(reference=tap_ref).exists())
        self.assertTrue(CardTapLog.objects.filter(tap_reference=tap_ref, status='APPROVED').exists())

    def test_duplicate_tap_deduplication(self):
        tap_ref = "tap-ref-duplicate"
        
        # First tap
        res1 = TapProcessor.process_card_tap(
            card_uid="RFID-12345",
            terminal_code="TERM-001",
            tap_reference=tap_ref
        )
        self.assertEqual(res1["status"], "APPROVED")
        
        # Second tap with same reference
        res2 = TapProcessor.process_card_tap(
            card_uid="RFID-12345",
            terminal_code="TERM-001",
            tap_reference=tap_ref
        )
        self.assertEqual(res2["status"], "APPROVED")
        self.assertEqual(res2["reason"], "Approval successful") # returned cached message

        # Only one debit ledger entry should exist
        self.assertEqual(WalletLedger.objects.filter(reference=f"DR-{tap_ref}").count(), 1)

    def test_unregistered_card_tap(self):
        tap_ref = "tap-ref-unregistered"
        result = TapProcessor.process_card_tap(
            card_uid="RFID-UNKNOWN",
            terminal_code="TERM-001",
            tap_reference=tap_ref
        )
        
        self.assertEqual(result["status"], "DECLINED")
        self.assertEqual(result["reason"], "Card unregistered")
        self.assertTrue(CardTapLog.objects.filter(tap_reference=tap_ref, status='DECLINED').exists())

    def test_blocked_card_tap(self):
        self.card.status = 'BLOCKED'
        self.card.save()
        
        tap_ref = "tap-ref-blocked"
        result = TapProcessor.process_card_tap(
            card_uid="RFID-12345",
            terminal_code="TERM-001",
            tap_reference=tap_ref
        )
        
        self.assertEqual(result["status"], "DECLINED")
        self.assertEqual(result["reason"], "Card is BLOCKED")

    def test_insufficient_balance(self):
        # Empty the wallet by adding a debit entry equal to current balance (1000)
        WalletLedger.objects.create(
            wallet=self.wallet,
            reference="DR-EMPTY",
            entry_type='DEBIT',
            amount=Decimal('1000.00'),
            description="Empty Wallet",
            source="TEST",
            source_id="2"
        )
        
        self.assertEqual(self.wallet.balance, Decimal('0.00'))
        
        tap_ref = "tap-ref-insufficient"
        result = TapProcessor.process_card_tap(
            card_uid="RFID-12345",
            terminal_code="TERM-001",
            tap_reference=tap_ref
        )
        
        self.assertEqual(result["status"], "DECLINED")
        self.assertEqual(result["reason"], "Insufficient balance")
        self.assertTrue(CardTapLog.objects.filter(tap_reference=tap_ref, status='DECLINED', response_message='Insufficient balance').exists())

