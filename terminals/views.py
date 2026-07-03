import json
import logging
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from terminals.services.tap_processor import TapProcessor

logger = logging.getLogger('terminals')

@csrf_exempt
@require_POST
def process_terminal_tap_view(request):
    """
    HTTP REST view called by transit vehicle Python Serial Bridges.
    Verifies balances and logs results in <300ms synchronously.
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "ERROR", "reason": "Invalid JSON"}, status=400)
        
    card_uid = data.get('card_uid')
    terminal_code = data.get('terminal_code')
    tap_reference = data.get('tap_reference')
    fare_amount = data.get('fare_amount')  # Optional route override
    
    if not card_uid or not terminal_code or not tap_reference:
        return JsonResponse({
            "status": "ERROR", 
            "reason": "Missing required fields: card_uid, terminal_code, tap_reference"
        }, status=400)
        
    logger.info(f"Card tap received. Card: {card_uid} | Terminal: {terminal_code} | Reference: {tap_reference}")
    
    result = TapProcessor.process_card_tap(
        card_uid=card_uid,
        terminal_code=terminal_code,
        tap_reference=tap_reference,
        fare_amount=fare_amount
    )
    
    status_code = 200 if result["status"] in ["APPROVED", "DECLINED"] else 400
    return JsonResponse(result, status=status_code)
