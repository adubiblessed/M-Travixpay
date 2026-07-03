from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from unittest.mock import patch, MagicMock
from decimal import Decimal

from wallets.models import Wallet, WalletLedger
from payments.models import VirtualAccount
from wallets.services.wallet_service import WalletService

User = get_user_model()

@override_settings(CACHES={
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'wallets-tests-cache',
    }
})
class WalletTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="passenger@travixpay.com",
            phone_number="+2348011111111",
            full_name="Passenger Test User",
            password="securepassword123"
        )

    def test_create_wallet_for_user(self):
        # Local wallet creation
        wallet = WalletService.create_wallet_for_user(self.user)
        self.assertEqual(wallet.user, self.user)
        self.assertEqual(wallet.status, 'PENDING_EXTERNAL_SETUP')
        self.assertEqual(wallet.currency, 'NGN')
        self.assertFalse(Wallet.objects.filter(user=self.user).count() > 1)

    @patch('payments.services.nomba_gateway.requests.request')
    @patch('payments.services.nomba_gateway.requests.post')
    def test_provision_virtual_account(self, mock_post, mock_request):
        # Mock Nomba auth and virtual account responses
        mock_auth_res = {
            "code": "00",
            "description": "Success",
            "data": {
                "accessToken": "test-access-token",
                "refreshToken": "test-refresh-token",
                "expiresIn": 1800
            }
        }
        mock_va_res = {
            "code": "00",
            "description": "Success",
            "data": {
                "bankAccountNumber": "1234567890",
                "bankAccountName": "TravixPay - Passenger Test User",
                "bankName": "Wema Bank",
                "accountHolderId": "holder-abc"
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
            if 'accounts/virtual' in url:
                res.json.return_value = mock_va_res
            return res

        mock_post.side_effect = mock_post_side_effect
        mock_request.side_effect = mock_request_side_effect

        # Pre-create local wallet in PENDING_EXTERNAL_SETUP status
        wallet = WalletService.create_wallet_for_user(self.user)
        
        # Provision virtual account
        WalletService.provision_virtual_account(self.user.uuid)
        
        # Refresh wallet and check if it's active
        wallet.refresh_from_db()
        self.assertEqual(wallet.status, 'ACTIVE')
        self.assertEqual(wallet.virtual_account_number, "1234567890")
        self.assertEqual(wallet.virtual_account_name, "TravixPay - Passenger Test User")
        self.assertEqual(wallet.virtual_account_provider, "Wema Bank")

        # Verify VirtualAccount record is created
        va = VirtualAccount.objects.get(wallet=wallet)
        self.assertEqual(va.account_number, "1234567890")
        self.assertEqual(va.status, 'ACTIVE')

