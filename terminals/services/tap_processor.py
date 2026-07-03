import logging
from decimal import Decimal
from django.db import transaction
from terminals.models import Terminal, FareRule, FareTransaction
from cards.models import RFIDCard, CardTapLog
from wallets.models import Wallet, WalletLedger
from events.services.event_dispatcher import EventDispatcher

logger = logging.getLogger('terminals')

class TapProcessor:
    @staticmethod
    def process_card_tap(card_uid, terminal_code, tap_reference, fare_amount=None):
        """
        Synchronously handles bus card taps to guarantee <300ms validation speed.
        Enforces unique constraints, locks database rows, and processes ledger debits.
        """
        # 1. Deduplication / Replay Check
        existing_log = CardTapLog.objects.filter(tap_reference=tap_reference).first()
        if existing_log:
            logger.info(f"Duplicate card tap request detected: {tap_reference}. Returning cached status.")
            return {
                "status": existing_log.status,
                "reason": existing_log.response_message,
                "reference": tap_reference,
                "fare": existing_log.fare_amount
            }

        # 2. Locate Terminal & Resolve Fare Rule
        try:
            terminal = Terminal.objects.get(terminal_code=terminal_code)
        except Terminal.DoesNotExist:
            logger.error(f"Unregistered terminal code: {terminal_code}")
            return {"status": "ERROR", "reason": f"Terminal {terminal_code} is not registered"}

        if fare_amount is not None:
            fare = Decimal(str(fare_amount))
        else:
            # Resolve fare from destination's FareRule
            rule = FareRule.objects.filter(route_name=terminal.route, is_active=True).first()
            if not rule:
                logger.error(f"No active FareRule for route '{terminal.route}' on terminal {terminal_code}")
                return {"status": "ERROR", "reason": f"No fare configured for route '{terminal.route}'"}
            fare = rule.amount

        # 3. Resolve RFID Card
        try:
            card = RFIDCard.objects.get(card_uid=card_uid)
        except RFIDCard.DoesNotExist:
            logger.warning(f"Card tapped but unregistered: {card_uid}")
            CardTapLog.objects.create(
                terminal=terminal,
                tap_reference=tap_reference,
                fare_amount=fare,
                status='DECLINED',
                response_message='Card unregistered'
            )
            return {"status": "DECLINED", "reason": "Card unregistered"}

        if card.status != 'ACTIVE':
            logger.warning(f"Card tapped but inactive: {card_uid} (Status: {card.status})")
            CardTapLog.objects.create(
                card=card,
                terminal=terminal,
                tap_reference=tap_reference,
                fare_amount=fare,
                status='DECLINED',
                response_message=f"Card is {card.status}"
            )
            return {"status": "DECLINED", "reason": f"Card is {card.status}"}

        # 4. Atomic Balance Check & Debit Execution
        try:
            with transaction.atomic():
                # Lock target wallet row to prevent double spending
                wallet = Wallet.objects.select_for_update().get(user=card.user)
                
                if wallet.status in ['LOCKED', 'SUSPENDED', 'CLOSED']:
                    raise ValueError(f"Wallet is in inactive state: {wallet.status}")
                
                balance = wallet.balance
                
                if balance < fare:
                    CardTapLog.objects.create(
                        card=card,
                        terminal=terminal,
                        tap_reference=tap_reference,
                        fare_amount=fare,
                        status='DECLINED',
                        response_message='Insufficient balance'
                    )
                    return {
                        "status": "DECLINED",
                        "reason": "Insufficient balance",
                        "reference": tap_reference,
                        "fare": fare,
                        "balance": balance
                    }

                # Record transaction debit in ledger
                ledger_ref = f"DR-{tap_reference}"
                ledger_entry = WalletLedger.objects.create(
                    wallet=wallet,
                    reference=ledger_ref,
                    entry_type='DEBIT',
                    amount=fare,
                    description=f"Transit fare deduction at terminal {terminal.terminal_code}",
                    source='FARE_DEDUCTION',
                    source_id=tap_reference
                )

                # Record FareTransaction
                FareTransaction.objects.create(
                    wallet=wallet,
                    card=card,
                    terminal=terminal,
                    reference=tap_reference,
                    fare_amount=fare,
                    ledger_entry=ledger_entry,
                    status='SUCCESS'
                )

                # Log tap status
                CardTapLog.objects.create(
                    card=card,
                    terminal=terminal,
                    tap_reference=tap_reference,
                    fare_amount=fare,
                    status='APPROVED',
                    response_message='Approval successful'
                )
                
                new_balance = balance - fare
                
            logger.info(f"Card {card_uid} tap APPROVED. Fare: ₦{fare}. Remaining balance: ₦{new_balance}")
            
            # 5. Dispatch FARE_DEDUCTED event outside of lock block
            EventDispatcher.dispatch(
                event_type='FARE_DEDUCTED',
                payload={
                    "card_uid": card_uid,
                    "terminal_code": terminal_code,
                    "tap_reference": tap_reference,
                    "fare_amount": str(fare),
                    "new_balance": str(new_balance)
                }
            )
            
            return {
                "status": "APPROVED",
                "reference": tap_reference,
                "fare": fare,
                "balance": new_balance
            }

        except Exception as e:
            logger.error(f"Error processing card tap: {e}")
            CardTapLog.objects.create(
                card=card,
                terminal=terminal,
                tap_reference=tap_reference,
                fare_amount=fare,
                status='ERROR',
                response_message=str(e)
            )
            return {"status": "ERROR", "reason": f"System error: {e}"}
