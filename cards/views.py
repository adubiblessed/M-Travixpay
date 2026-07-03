from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import IntegrityError
from cards.models import RFIDCard

@login_required
def manage_view(request):
    if request.user.role != 'PASSENGER':
        return redirect('accounts:login')
        
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'register':
            card_uid = request.POST.get('card_uid', '').strip()
            if not card_uid:
                messages.error(request, "Card UID cannot be empty.")
            else:
                try:
                    # Check if card is already registered
                    if RFIDCard.objects.filter(card_uid=card_uid).exists():
                        messages.error(request, "This card UID is already linked to an account.")
                    else:
                        RFIDCard.objects.create(
                            user=request.user,
                            card_uid=card_uid,
                            status='ACTIVE'
                        )
                        messages.success(request, f"Card {card_uid} linked successfully!")
                except Exception as e:
                    messages.error(request, f"Failed to link card: {str(e)}")
                    
        elif action == 'block':
            card_id = request.POST.get('card_id')
            card = get_object_or_404(RFIDCard, id=card_id, user=request.user)
            card.status = 'BLOCKED'
            card.save(update_fields=['status'])
            messages.warning(request, f"Card {card.card_uid} has been blocked.")
            
        elif action == 'unblock':
            card_id = request.POST.get('card_id')
            card = get_object_or_404(RFIDCard, id=card_id, user=request.user)
            card.status = 'ACTIVE'
            card.save(update_fields=['status'])
            messages.success(request, f"Card {card.card_uid} is now active.")
            
        return redirect('cards:manage')
        
    cards = RFIDCard.objects.filter(user=request.user).order_by('-linked_at')
    return render(request, 'pages/cards/manage.html', {'cards': cards})
