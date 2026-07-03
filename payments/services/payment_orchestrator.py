import logging
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from payments.models import PaymentIntent, PaymentTransaction
from payments.services.nomba_gateway import NombaGateway
from wallets.models import WalletLedger
from audit.models import AuditLog

logger = logging.getLogger('payments')


def _get_base_url():
    """
    Returns the absolute base URL for this deployment.
    Checks SITE_URL setting first, falls back to localhost for dev.
    """
    site_url = getattr(settings, 'SITE_URL', '')
    if site_url:
        return site_url.rstrip('/')
    # Sandbox/dev fallback
    return 'http://127.0.0.1:8000'


class PaymentOrchestrator:
    @staticmethod
    def create_checkout_session(payment_intent_uuid):
        """
        Creates a hosted checkout link via Nomba.
        Called synchronously from EventDispatcher.
        """
        try:
            intent = PaymentIntent.objects.get(uuid=payment_intent_uuid)
        except PaymentIntent.DoesNotExist:
            logger.error(f"PaymentIntent with UUID {payment_intent_uuid} not found.")
            return

        if intent.status != 'CREATED':
            logger.warning(f"PaymentIntent {intent.reference} is in status {intent.status}, expected CREATED.")
            return

        with transaction.atomic():
            intent = PaymentIntent.objects.select_for_update().get(id=intent.id)
            intent.status = 'PROCESSING'
            intent.save(update_fields=['status'])

        gateway = NombaGateway()

        # Build absolute callback URL (Nomba requires full URL, not relative path)
        base_url = _get_base_url()
        callback_url = f"{base_url}/payments/callback/?reference={intent.reference}"

        try:
            # Debug: log all URLs before calling Nomba
            logger.info(f"=== CHECKOUT URL DEBUG for {intent.reference} ===")
            logger.info(f"  Callback URL: {callback_url}")
            logger.info(f"  Amount: {intent.amount}")
            logger.info(f"  Base URL: {base_url}")

            res = gateway.create_checkout_order(
                amount=intent.amount,
                reference=intent.reference,
                callback_url=callback_url,
                customer_email=intent.user.email
            )
            data = res['data']
            checkout_link = data['checkoutLink']

            logger.info(f"  Checkout URL (from Nomba): {checkout_link}")
            logger.info(f"  Order Reference (from Nomba): {data.get('orderReference')}")
            logger.info(f"=== END CHECKOUT URL DEBUG ===")

            with transaction.atomic():
                intent = PaymentIntent.objects.select_for_update().get(id=intent.id)
                intent.checkout_url = checkout_link
                intent.status = 'AWAITING_PAYMENT'
                intent.save(update_fields=['checkout_url', 'status'])

            logger.info(f"Checkout link generated for {intent.reference}: {checkout_link}")

        except Exception as e:
            logger.error(f"Failed to create checkout for {intent.reference}: {e}")
            with transaction.atomic():
                intent = PaymentIntent.objects.select_for_update().get(id=intent.id)
                intent.status = 'FAILED'
                intent.save(update_fields=['status'])
            raise

    @staticmethod
    def verify_and_credit(intent, order_id=None):
        """
        VERIFY_ONLY mode: Verify payment with Nomba API and credit wallet.
        Called synchronously from callback view.

        Args:
            intent: PaymentIntent instance
            order_id: Nomba's orderId from callback params (optional, used as fallback)

        Returns True if payment was confirmed and wallet credited.
        Returns False if payment was not confirmed.
        """
        if intent.status == 'SUCCESS':
            logger.info(f"PaymentIntent {intent.reference} already SUCCESS.")
            return True

        gateway = NombaGateway()

        # Try verification with orderReference first
        try:
            logger.info(f"=== VERIFICATION DEBUG for {intent.reference} ===")
            logger.info(f"  Attempting verify with ORDER_REFERENCE: {intent.reference}")
            res = gateway.fetch_checkout_transaction(intent.reference, id_type="ORDER_REFERENCE")
            logger.info(f"  Verification response: {res}")
        except Exception as e:
            logger.error(f"  Verification API call failed: {e}")
            res = None

        data = res.get('data') if res else None

        # If no data with ORDER_REFERENCE, try with orderId
        if not data and order_id:
            try:
                logger.info(f"  No data with ORDER_REFERENCE, trying ORDER_ID: {order_id}")
                res = gateway.fetch_checkout_transaction(order_id, id_type="ORDER_ID")
                logger.info(f"  ORDER_ID response: {res}")
                data = res.get('data') if res else None
            except Exception as e:
                logger.error(f"  ORDER_ID verification failed: {e}")

        if not data:
            logger.warning(f"  No verification data found for {intent.reference}")
            logger.info(f"=== END VERIFICATION DEBUG ===")
            return False

        logger.info(f"  Verification data fields: {list(data.keys()) if isinstance(data, dict) else data}")

        # Nomba sandbox response format:
        # {
        #   "code": "00", "status": true, "data": {
        #     "success": true,
        #     "order": { "orderId": "...", "amount": "1000.00" },
        #     "transactionDetails": { "paymentReference": "..." }
        #   }
        # }
        # The 'data' we receive is already res['data'], so check nested 'data' inside it
        inner_data = data.get('data', data)  # Handle nested data
        if isinstance(inner_data, dict) and 'success' in inner_data:
            # Nested format: res['data']['data']
            inner_data = inner_data

        # Check success indicators at multiple levels
        is_success = False

        # Level 1: res['data']['success'] == True
        if isinstance(data.get('success'), bool):
            is_success = data['success']
            logger.info(f"  Success from data['success']: {is_success}")

        # Level 2: res['data']['data']['success'] == True
        if not is_success and isinstance(inner_data, dict):
            if isinstance(inner_data.get('success'), bool):
                is_success = inner_data['success']
                logger.info(f"  Success from data['data']['success']: {is_success}")

        # Level 3: res['status'] == True (top-level)
        if not is_success and isinstance(res.get('status'), bool):
            is_success = res['status']
            logger.info(f"  Success from res['status']: {is_success}")

        # Level 4: res['code'] == '00'
        if not is_success and res.get('code') == '00':
            is_success = True
            logger.info(f"  Success from res['code'] == '00'")

        # Level 5: Check nested data fields
        if not is_success:
            for status_field in ['status', 'transactionStatus', 'paymentStatus', 'orderStatus']:
                val = data.get(status_field, '')
                if val and str(val).upper() in ('SUCCESS', 'SUCCESSFUL', 'PAID', 'COMPLETED', 'APPROVED', 'TRUE'):
                    is_success = True
                    logger.info(f"  Success from data['{status_field}']: {val}")
                    break

        logger.info(f"  Final is_success: {is_success}")
        logger.info(f"=== END VERIFICATION DEBUG ===")

        if not is_success:
            logger.info(f"Payment not confirmed for {intent.reference}")
            return False

        # Extract amount from response
        order_data = data.get('order', inner_data.get('order', {}) if isinstance(inner_data, dict) else {})
        amount = order_data.get('amount') or data.get('amount') or intent.amount
        if isinstance(amount, str):
            amount = Decimal(amount)

        # Extract transaction ID
        tx_details = data.get('transactionDetails', {})
        provider_tx_id = (
            tx_details.get('paymentReference', '')
            or tx_details.get('paymentVendorReference', '')
            or order_data.get('orderId', '')
            or data.get('transactionId', '')
        )

        # Credit wallet atomically
        return PaymentOrchestrator._credit_wallet(
            intent=intent,
            provider_reference=intent.reference,
            provider_tx_id=provider_tx_id,
            amount=amount,
            payment_method='CARD',
            raw_payload=res
        )

    @staticmethod
    def process_payment_success(payment_intent, provider_reference, provider_tx_id, amount, payment_method, raw_payload):
        """
        WEBHOOK_REQUIRED mode: Credit wallet from webhook confirmation.
        Called from WebhookProcessor.
        """
        if payment_intent.status == 'SUCCESS':
            logger.info(f"PaymentIntent {payment_intent.reference} already SUCCESS.")
            return True

        return PaymentOrchestrator._credit_wallet(
            intent=payment_intent,
            provider_reference=provider_reference,
            provider_tx_id=provider_tx_id,
            amount=amount,
            payment_method=payment_method,
            raw_payload=raw_payload
        )

    @staticmethod
    def _credit_wallet(intent, provider_reference, provider_tx_id, amount, payment_method, raw_payload):
        """
        Internal: Credit wallet atomically.
        Uses SELECT FOR UPDATE to prevent double crediting.
        """
        try:
            with transaction.atomic():
                locked_intent = PaymentIntent.objects.select_for_update().get(id=intent.id)

                if locked_intent.status == 'SUCCESS':
                    logger.info(f"PaymentIntent {locked_intent.reference} already SUCCESS (race check).")
                    return True

                wallet = locked_intent.wallet
                locked_wallet = type(wallet).objects.select_for_update().get(id=wallet.id)

                # Update PaymentIntent
                locked_intent.status = 'SUCCESS'
                locked_intent.save(update_fields=['status'])

                # Create PaymentTransaction
                PaymentTransaction.objects.update_or_create(
                    payment_intent=locked_intent,
                    provider_reference=provider_reference,
                    defaults={
                        'provider_transaction_id': provider_tx_id,
                        'amount': amount,
                        'payment_method': payment_method,
                        'status': 'SUCCESS',
                        'raw_response': str(raw_payload),
                        'processed_at': timezone.now()
                    }
                )

                # Create ledger entry (idempotent via unique reference)
                ledger_ref = f"CR-{provider_reference}"
                WalletLedger.objects.get_or_create(
                    reference=ledger_ref,
                    defaults={
                        'wallet': locked_wallet,
                        'entry_type': 'CREDIT',
                        'amount': amount,
                        'description': f"Wallet funding via Nomba Checkout. Ref: {provider_reference}",
                        'source': 'PAYMENT',
                        'source_id': str(locked_intent.id)
                    }
                )

            # Audit log (outside lock)
            AuditLog.objects.create(
                user=intent.user,
                action='WALLET_FUNDED',
                details={
                    'amount': str(amount),
                    'reference': provider_reference,
                    'method': payment_method
                }
            )

            logger.info(f"Credited wallet {locked_wallet.uuid} with ₦{amount} for {intent.reference}")
            return True

        except Exception as e:
            logger.error(f"Failed to credit wallet for {intent.reference}: {e}")
            raise
