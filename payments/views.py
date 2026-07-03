import uuid
import json
import logging
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db import IntegrityError, transaction

from events.models import WebhookEvent
from events.services.event_dispatcher import EventDispatcher
from payments.models import PaymentIntent
from payments.services.webhook_processor import WebhookProcessor
from payments.services.payment_orchestrator import PaymentOrchestrator

logger = logging.getLogger('payments')


def _get_confirmation_mode():
    return getattr(settings, 'PAYMENT_CONFIRMATION_MODE', 'VERIFY_ONLY')


@csrf_exempt
@require_POST
def nomba_webhook_view(request):
    """
    Webhook endpoint.
    Only active in WEBHOOK_REQUIRED mode.
    In VERIFY_ONLY mode, returns disabled.
    """
    if _get_confirmation_mode() != 'WEBHOOK_REQUIRED':
        return JsonResponse({"status": "disabled", "mode": "VERIFY_ONLY"}, status=200)

    signature_header = request.headers.get('nomba-signature')
    raw_body = request.body

    if not WebhookProcessor.verify_signature(raw_body, signature_header):
        logger.warning("Rejected webhook: invalid signature.")
        return HttpResponse("Unauthorized", status=401)

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return HttpResponse("Bad Request: Invalid JSON", status=400)

    request_id = payload.get('requestId')
    event_type = payload.get('event_type') or payload.get('event')

    if not request_id or not event_type:
        return HttpResponse("Bad Request: Missing parameters", status=400)

    try:
        webhook_event = WebhookEvent.objects.create(
            provider='NOMBA',
            event_id=request_id,
            event_type=event_type,
            signature=signature_header or 'N/A',
            payload=payload,
            processing_status='RECEIVED'
        )
    except IntegrityError:
        logger.info(f"Duplicate webhook ignored: {request_id}")
        return HttpResponse("Event already received", status=200)

    EventDispatcher.dispatch(
        event_type='WEBHOOK_RECEIVED',
        payload={"webhook_event_id": webhook_event.id}
    )

    return HttpResponse("Acknowledged", status=200)


@login_required
def fund_start_view(request):
    if request.user.role != 'PASSENGER':
        return redirect('accounts:login')

    if request.method == 'POST':
        amount_str = request.POST.get('amount')
        try:
            amount = Decimal(amount_str)
            if amount < 100 or amount > 50000:
                raise ValueError("Amount out of limits")
            request.session['funding_amount'] = str(amount)
            return redirect('payments:fund_review')
        except (TypeError, ValueError, InvalidOperation):
            messages.error(request, "Please enter a valid amount between ₦100 and ₦50,000.")

    return render(request, 'pages/wallet/fund_start.html')


@login_required
def fund_review_view(request):
    if request.user.role != 'PASSENGER':
        return redirect('accounts:login')

    amount_str = request.session.get('funding_amount')
    if not amount_str:
        return redirect('payments:fund_start')

    if request.method == 'POST':
        amount = Decimal(amount_str)
        reference = f"TX-{uuid.uuid4().hex[:12].upper()}"

        with transaction.atomic():
            intent = PaymentIntent.objects.create(
                user=request.user,
                wallet=request.user.wallet,
                reference=reference,
                amount=amount,
                expires_at=timezone.now() + timezone.timedelta(minutes=20),
                status='CREATED'
            )

        EventDispatcher.dispatch(
            event_type='PAYMENT_INTENT_CREATED',
            payload={"payment_intent_uuid": str(intent.uuid)}
        )

        request.session.pop('funding_amount', None)
        return redirect('payments:fund_loading', intent_uuid=str(intent.uuid))

    return render(request, 'pages/wallet/fund_review.html', {'amount': amount_str})


@login_required
def fund_loading_view(request, intent_uuid):
    intent = get_object_or_404(PaymentIntent, uuid=intent_uuid, user=request.user)
    return render(request, 'pages/wallet/fund_loading.html', {'intent': intent})


@login_required
def fund_poll_view(request, intent_uuid):
    intent = get_object_or_404(PaymentIntent, uuid=intent_uuid, user=request.user)

    age_seconds = (timezone.now() - intent.created_at).total_seconds()
    if age_seconds > 90 and intent.status in ('CREATED', 'PROCESSING'):
        intent.status = 'FAILED'
        intent.save(update_fields=['status'])
        logger.warning(f"PaymentIntent {intent.reference} timed out after {int(age_seconds)}s")

    if intent.status == 'AWAITING_PAYMENT' and intent.checkout_url:
        logger.info(f"=== REDIRECT DEBUG for {intent.reference} ===")
        logger.info(f"  Checkout URL: {intent.checkout_url}")
        logger.info(f"  Intent Status: {intent.status}")
        logger.info(f"=== END REDIRECT DEBUG ===")
        response = HttpResponse()
        response['HX-Redirect'] = intent.checkout_url
        return response
    elif intent.status == 'FAILED':
        logger.warning(f"PaymentIntent {intent.reference} FAILED, redirecting to failure page")
        response = HttpResponse()
        response['HX-Redirect'] = '/wallet/fund/failure/'
        return response

    return render(request, 'partials/funding_loading_status.html', {'intent': intent})


@login_required
def fund_success_view(request):
    reference = request.GET.get('reference')
    intent = None
    if reference:
        intent = PaymentIntent.objects.filter(reference=reference, user=request.user).first()
    return render(request, 'pages/wallet/fund_success.html', {'intent': intent})


@login_required
def fund_failure_view(request):
    return render(request, 'pages/wallet/fund_failure.html')


@login_required
def payment_callback_view(request):
    """
    Nomba redirects user here after checkout.
    VERIFY_ONLY: verify via API, credit wallet, redirect to success.
    WEBHOOK_REQUIRED: just redirect to pending/success based on webhook status.
    """
    reference = request.GET.get('reference')
    order_id = request.GET.get('orderId') or request.GET.get('orderReference')

    logger.info(f"=== CALLBACK DEBUG ===")
    logger.info(f"  Full request path: {request.get_full_path()}")
    logger.info(f"  Reference param: {reference}")
    logger.info(f"  Order ID param: {order_id}")
    logger.info(f"  GET params: {dict(request.GET)}")
    logger.info(f"=== END CALLBACK DEBUG ===")

    if not reference:
        logger.warning("Callback received with no reference parameter")
        return redirect('accounts:passenger_dashboard')

    intent = get_object_or_404(PaymentIntent, reference=reference, user=request.user)

    logger.info(f"Callback for {reference}: status={intent.status}, amount={intent.amount}")

    if intent.status == 'SUCCESS':
        messages.success(request, "Payment verified successfully!")
        return redirect(f'/wallet/fund/success/?reference={reference}')

    mode = _get_confirmation_mode()
    logger.info(f"Callback mode: {mode}")

    if mode == 'VERIFY_ONLY':
        # Synchronous verification: call Nomba API, credit wallet immediately
        try:
            confirmed = PaymentOrchestrator.verify_and_credit(intent, order_id=order_id)
            if confirmed:
                messages.success(request, "Payment verified and wallet credited!")
                return redirect(f'/wallet/fund/success/?reference={reference}')
            else:
                messages.warning(request, "Payment could not be verified. Please try again.")
                return redirect('payments:fund_failure')
        except Exception as e:
            logger.error(f"Callback verification failed for {reference}: {e}")
            messages.error(request, "Verification error. Contact support if payment was deducted.")
            return redirect('payments:fund_failure')

    else:
        # WEBHOOK_REQUIRED: webhook is source of truth
        # If we got here and status isn't SUCCESS, webhook hasn't arrived yet
        if intent.status in ('AWAITING_PAYMENT', 'AWAITING_WEBHOOK'):
            messages.info(request, "Payment received. Your wallet will be credited shortly.")
        return redirect('accounts:passenger_dashboard')
