from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.conf import settings
from django.urls import reverse
from unittest.mock import patch, MagicMock
from decimal import Decimal
import hmac
import hashlib
import json
import time

from wallets.models import Wallet, WalletLedger
from payments.models import PaymentIntent, PaymentTransaction, VirtualAccount
from events.models import DomainEvent, WebhookEvent
from payments.services.nomba_gateway import (
    NombaGateway, NombaGatewayError, NombaAuthenticationError,
    NombaRateLimitError, NombaCircuitBreakerOpenError
)
from payments.services.payment_orchestrator import PaymentOrchestrator
from payments.services.webhook_processor import WebhookProcessor

User = get_user_model()

@override_settings(CACHES={
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'payment-tests-cache',
    }
})
class PaymentTests(TestCase):

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = User.objects.create_user(
            email="passenger@travixpay.com",
            phone_number="+2348011111111",
            full_name="Passenger Test User",
            password="securepassword123"
        )
        # Create a wallet for the user
        self.wallet = Wallet.objects.create(
            user=self.user,
            status='ACTIVE',
            currency='NGN'
        )
        self.client = Client()

    @patch('payments.services.nomba_gateway.requests.post')
    def test_nomba_gateway_authentication(self, mock_post):
        # Mock successful token generation
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": "00",
            "description": "Success",
            "data": {
                "accessToken": "test-access-token",
                "refreshToken": "test-refresh-token",
                "expiresIn": 1800
            }
        }
        mock_post.return_value = mock_response

        gateway = NombaGateway()
        token = gateway._get_access_token()
        self.assertEqual(token, "test-access-token")
        
        # Verify it fetched token using issue endpoint
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertIn('/v1/auth/token/issue', args[0])

    @patch('payments.services.nomba_gateway.requests.request')
    @patch('payments.services.nomba_gateway.requests.post')
    def test_nomba_gateway_create_checkout_order(self, mock_post, mock_request):
        # Mock auth and checkout endpoints
        mock_auth_res = {
            "code": "00",
            "description": "Success",
            "data": {
                "accessToken": "test-access-token",
                "refreshToken": "test-refresh-token",
                "expiresIn": 1800
            }
        }
        mock_checkout_res = {
            "code": "00",
            "description": "Success",
            "data": {
                "checkoutLink": "https://checkout.nomba.com/pay/abc",
                "orderReference": "TX-12345"
            }
        }
        
        def mock_post_side_effect(url, *args, **kwargs):
            res = MagicMock()
            res.status_code = 200
            if 'auth/token/issue' in url:
                res.json.return_value = mock_auth_res
            return res

        def mock_request_side_effect(method, url, *args, **kwargs):
            res = MagicMock()
            res.status_code = 200
            if 'checkout/order' in url:
                res.json.return_value = mock_checkout_res
            return res

        mock_post.side_effect = mock_post_side_effect
        mock_request.side_effect = mock_request_side_effect

        gateway = NombaGateway()
        res = gateway.create_checkout_order(
            amount=Decimal('500.00'),
            reference="TX-12345",
            callback_url="https://travixpay.com/callback",
            customer_email="passenger@travixpay.com"
        )
        self.assertEqual(res['code'], '00')
        self.assertEqual(res['data']['checkoutLink'], "https://checkout.nomba.com/pay/abc")

    @patch('payments.services.nomba_gateway.requests.request')
    @patch('payments.services.nomba_gateway.requests.post')
    def test_payment_orchestrator_create_checkout_session(self, mock_post, mock_request):
        # Setup mocks
        mock_auth_res = {
            "code": "00",
            "description": "Success",
            "data": {
                "accessToken": "test-access-token",
                "refreshToken": "test-refresh-token",
                "expiresIn": 1800
            }
        }
        mock_checkout_res = {
            "code": "00",
            "description": "Success",
            "data": {
                "checkoutLink": "https://checkout.nomba.com/pay/abc",
                "orderReference": "TX-12345"
            }
        }

        def mock_post_side_effect(url, *args, **kwargs):
            res = MagicMock()
            res.status_code = 200
            if 'auth/token/issue' in url:
                res.json.return_value = mock_auth_res
            return res

        def mock_request_side_effect(method, url, *args, **kwargs):
            res = MagicMock()
            res.status_code = 200
            if 'checkout/order' in url:
                res.json.return_value = mock_checkout_res
            return res

        mock_post.side_effect = mock_post_side_effect
        mock_request.side_effect = mock_request_side_effect

        # Create a PaymentIntent
        intent = PaymentIntent.objects.create(
            user=self.user,
            wallet=self.wallet,
            reference="TX-12345",
            amount=Decimal('1500.00'),
            expires_at=timezone.now() + timezone.timedelta(minutes=30),
            status='CREATED'
        )

        PaymentOrchestrator.create_checkout_session(intent.uuid)
        
        # Verify it transitioned status and saved checkout URL
        intent.refresh_from_db()
        self.assertEqual(intent.status, 'AWAITING_PAYMENT')
        self.assertEqual(intent.checkout_url, "https://checkout.nomba.com/pay/abc")

    def test_payment_orchestrator_process_payment_success(self):
        intent = PaymentIntent.objects.create(
            user=self.user,
            wallet=self.wallet,
            reference="TX-67890",
            amount=Decimal('2000.00'),
            expires_at=timezone.now() + timezone.timedelta(minutes=30),
            status='AWAITING_PAYMENT'
        )

        # Confirm wallet starts with 0 balance
        self.assertEqual(self.wallet.balance, Decimal('0.00'))

        # Call payment success processing
        success = PaymentOrchestrator.process_payment_success(
            payment_intent=intent,
            provider_reference="TX-67890",
            provider_tx_id="nomba-tx-abc",
            amount=Decimal('2000.00'),
            payment_method="CARD",
            raw_payload={"test": "data"}
        )

        self.assertTrue(success)
        
        # Refresh and assert status transitions
        intent.refresh_from_db()
        self.assertEqual(intent.status, 'SUCCESS')

        # Assert wallet ledger credit record exists
        ledger = WalletLedger.objects.get(reference="CR-TX-67890")
        self.assertEqual(ledger.wallet, self.wallet)
        self.assertEqual(ledger.entry_type, 'CREDIT')
        self.assertEqual(ledger.amount, Decimal('2000.00'))

        # Assert wallet balance is computed correctly
        self.assertEqual(self.wallet.balance, Decimal('2000.00'))

    def test_webhook_processor_verify_signature(self):
        # Test signature verification
        secret = settings.NOMBA_WEBHOOK_SECRET
        raw_body = b'{"requestId":"req-123","event":"payment_success"}'
        
        expected_sig = hmac.new(
            secret.encode('utf-8'),
            raw_body,
            hashlib.sha256
        ).hexdigest()

        self.assertTrue(WebhookProcessor.verify_signature(raw_body, expected_sig))
        self.assertFalse(WebhookProcessor.verify_signature(raw_body, "invalid-sig"))

    @patch('payments.services.nomba_gateway.requests.request')
    @patch('payments.services.nomba_gateway.requests.post')
    def test_circuit_breaker_tripping_on_repeated_failures(self, mock_post, mock_request):
        # Mock successful authentication so we don't trip on auth issues
        mock_auth_response = MagicMock()
        mock_auth_response.status_code = 200
        mock_auth_response.json.return_value = {
            "code": "00",
            "description": "Success",
            "data": {
                "accessToken": "test-access-token",
                "refreshToken": "test-refresh-token",
                "expiresIn": 1800
            }
        }
        mock_post.return_value = mock_auth_response

        # Mock request failures (consecutive 500 errors) to trigger circuit breaker
        mock_req_response = MagicMock()
        mock_req_response.status_code = 500
        mock_req_response.json.return_value = {
            "code": "96",
            "description": "System error"
        }
        mock_request.return_value = mock_req_response

        gateway = NombaGateway()

        # Call create_checkout_order 5 times to trigger circuit breaker
        for _ in range(5):
            with self.assertRaises(NombaGatewayError):
                gateway.create_checkout_order(
                    amount=Decimal('100.00'),
                    reference="TX-BREAKER",
                    callback_url="https://test.com"
                )

        # Next call should raise NombaCircuitBreakerOpenError (fast-fail)
        with self.assertRaises(NombaCircuitBreakerOpenError):
            gateway.create_checkout_order(
                amount=Decimal('100.00'),
                reference="TX-BREAKER",
                callback_url="https://test.com"
            )

