import logging
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from wallets.models import Wallet
from wallets.services.wallet_service import WalletService

logger = logging.getLogger('payments')


@login_required
def virtual_account_detail(request):
    if request.user.role != 'PASSENGER':
        return redirect('accounts:login')

    try:
        wallet = request.user.wallet
    except Wallet.DoesNotExist:
        return redirect('accounts:passenger_dashboard')

    return render(request, 'pages/wallet/virtual_account.html', {'wallet': wallet})


@login_required
def create_virtual_account(request):
    """
    POST endpoint: provisions a Nomba virtual account for the user.
    Works in sandbox mode (no auth required).
    """
    if request.user.role != 'PASSENGER':
        return redirect('accounts:login')

    if request.method != 'POST':
        return redirect('wallets:virtual_account_detail')

    try:
        wallet = request.user.wallet
    except Wallet.DoesNotExist:
        messages.error(request, "Wallet not found.")
        return redirect('accounts:passenger_dashboard')

    if wallet.virtual_account_number:
        messages.info(request, "You already have a virtual account.")
        return redirect('wallets:virtual_account_detail')

    # Call the service to provision virtual account via Nomba
    try:
        WalletService.provision_virtual_account(str(request.user.uuid))
        wallet.refresh_from_db()
        if wallet.virtual_account_number:
            messages.success(request, f"Virtual account created! Account number: {wallet.virtual_account_number}")
        else:
            messages.warning(request, "Virtual account provisioning initiated. It may take a moment to activate.")
    except Exception as e:
        logger.error(f"Virtual account creation failed for {request.user.email}: {e}")
        messages.error(request, "Failed to create virtual account. Please try again.")

    return redirect('wallets:virtual_account_detail')


@login_required
def balance_partial(request):
    if request.user.role != 'PASSENGER':
        return redirect('accounts:login')

    try:
        wallet = request.user.wallet
    except Wallet.DoesNotExist:
        return redirect('accounts:passenger_dashboard')

    return render(request, 'partials/wallet_balance.html', {'wallet': wallet})
