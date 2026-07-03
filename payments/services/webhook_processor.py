import hmac
import hashlib
import logging
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from events.models import WebhookEvent
from payments.models import PaymentIntent
from payments.services.payment_orchestrator import PaymentOrchestrator

logger = logging.getLogger('payments')

class WebhookProcessor:
    @staticmethod
    def verify_signature(raw_body, signature_header):
        """
        Validates HMAC-SHA256 signature of the raw request body 
        using the NOMBA_WEBHOOK_SECRET.
        """
        secret = getattr(settings, 'NOMBA_WEBHOOK_SECRET', '')
        if not secret:
            logger.warning("NOMBA_WEBHOOK_SECRET is not configured. Webhook validation bypassed.")
            return True
            
        if not signature_header:
            return False
            
        calculated_signature = hmac.new(
            secret.encode('utf-8'),
            raw_body,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(calculated_signature, signature_header)

    @staticmethod
    def process_webhook_payload(webhook_event_id):
        """
        Parses the stored raw webhook payload and applies funding credit 
        movements asynchronously in the Celery background worker.
        """
        try:
            webhook_event = WebhookEvent.objects.get(id=webhook_event_id)
        except WebhookEvent.DoesNotExist:
            logger.error(f"WebhookEvent with ID {webhook_event_id} not found.")
            return

        if webhook_event.processing_status in ['PROCESSED', 'IGNORED']:
            logger.info(f"WebhookEvent {webhook_event.event_id} already in final state: {webhook_event.processing_status}.")
            return

        # Signature was already verified before saving, transition status
        webhook_event.processing_status = 'VERIFIED'
        webhook_event.save(update_fields=['processing_status'])

        payload = webhook_event.payload
        event_type = payload.get('event_type') or payload.get('event')
        
        # We only handle payment_success events for funding
        if event_type != 'payment_success':
            logger.info(f"Ignoring unhandled webhook event: {event_type}")
            webhook_event.processing_status = 'IGNORED'
            webhook_event.processed_at = timezone.now()
            webhook_event.save(update_fields=['processing_status', 'processed_at'])
            return

        data = payload.get('data', {})
        order_data = data.get('order', {})
        tx_data = data.get('transaction', {})

        order_ref = order_data.get('orderReference') or tx_data.get('merchantTxRef')
        amount_str = order_data.get('amount') or tx_data.get('transactionAmount')
        provider_tx_id = tx_data.get('transactionId')
        payment_method = order_data.get('paymentMethod', 'UNKNOWN')

        if not order_ref or not amount_str:
            logger.error(f"Webhook {webhook_event.event_id} payload missing reference/amount details.")
            webhook_event.processing_status = 'FAILED'
            webhook_event.processed_at = timezone.now()
            webhook_event.save(update_fields=['processing_status', 'processed_at'])
            return

        try:
            amount = Decimal(str(amount_str))
        except (ValueError, TypeError, InvalidOperation):
            logger.error(f"Webhook {webhook_event.event_id} has invalid amount data: {amount_str}")
            webhook_event.processing_status = 'FAILED'
            webhook_event.processed_at = timezone.now()
            webhook_event.save(update_fields=['processing_status', 'processed_at'])
            return

        try:
            intent = PaymentIntent.objects.get(reference=order_ref)
        except PaymentIntent.DoesNotExist:
            logger.error(f"PaymentIntent with reference {order_ref} not found for Webhook {webhook_event.event_id}.")
            webhook_event.processing_status = 'FAILED'
            webhook_event.processed_at = timezone.now()
            webhook_event.save(update_fields=['processing_status', 'processed_at'])
            return

        try:
            # Complete the funding workflow atomically
            success = PaymentOrchestrator.process_payment_success(
                payment_intent=intent,
                provider_reference=order_ref,
                provider_tx_id=provider_tx_id,
                amount=amount,
                payment_method=payment_method,
                raw_payload=payload
            )
            
            if success:
                webhook_event.processing_status = 'PROCESSED'
            else:
                webhook_event.processing_status = 'FAILED'
                
        except Exception as e:
            logger.error(f"Database error executing payment credit: {e}")
            webhook_event.processing_status = 'FAILED'
            raise e
        finally:
            webhook_event.processed_at = timezone.now()
            webhook_event.save(update_fields=['processing_status', 'processed_at'])
