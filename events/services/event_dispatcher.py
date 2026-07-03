import logging
from django.db import transaction
from events.models import DomainEvent

logger = logging.getLogger('payments')

class EventDispatcher:
    @staticmethod
    def dispatch(event_type, payload):
        """
        Stores a DomainEvent and processes it synchronously.
        No Celery/Redis — hackathon MVP uses direct handler calls.
        """
        event = DomainEvent.objects.create(
            event_type=event_type,
            payload=payload,
            status='PENDING'
        )

        def _process():
            try:
                logger.info(f"Processing event {event_type} (ID: {event.event_id})")
                event.status = 'PROCESSING'
                event.save(update_fields=['status'])

                if event.event_type == 'USER_REGISTERED':
                    from wallets.services.wallet_service import WalletService
                    WalletService.provision_virtual_account(event.payload['user_uuid'])

                elif event.event_type == 'PAYMENT_INTENT_CREATED':
                    from payments.services.payment_orchestrator import PaymentOrchestrator
                    PaymentOrchestrator.create_checkout_session(event.payload['payment_intent_uuid'])

                elif event.event_type == 'WEBHOOK_RECEIVED':
                    from payments.services.webhook_processor import WebhookProcessor
                    WebhookProcessor.process_webhook_payload(event.payload['webhook_event_id'])

                else:
                    logger.warning(f"Unhandled event type: {event.event_type}")

                event.status = 'COMPLETED'
                event.save(update_fields=['status'])
                logger.info(f"Completed event {event_type} (ID: {event.event_id})")

            except Exception as exc:
                logger.error(f"Event {event_type} failed (ID: {event.event_id}): {exc}")
                event.status = 'FAILED'
                event.save(update_fields=['status'])

        transaction.on_commit(_process)
        logger.info(f"Created event {event_type} with ID: {event.event_id}")
        return event
