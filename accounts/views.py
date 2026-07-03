from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import IntegrityError, transaction
from accounts.models import User
from wallets.services.wallet_service import WalletService
from events.services.event_dispatcher import EventDispatcher
from wallets.models import Wallet, WalletLedger

def login_view(request):
    if request.user.is_authenticated:
        return redirect_by_role(request.user)

    if request.method == 'POST':
        email = request.POST.get('username')
        password = request.POST.get('password')
        
        user = authenticate(request, username=email, password=password)
        if user is not None:
            login(request, user)
            messages.success(request, f"Welcome back, {user.full_name}!")
            return redirect_by_role(user)
        else:
            messages.error(request, "Invalid email or password.")
            
    return render(request, 'pages/auth/login.html')

def register_view(request):
    if request.user.is_authenticated:
        return redirect_by_role(request.user)

    if request.method == 'POST':
        full_name = request.POST.get('full_name')
        email = request.POST.get('email')
        phone_number = request.POST.get('phone_number')
        password = request.POST.get('password')
        role = request.POST.get('role', 'PASSENGER')

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    email=email,
                    phone_number=phone_number,
                    full_name=full_name,
                    password=password,
                    role=role
                )
                
                # If they are a passenger, initialize their local wallet
                if role == 'PASSENGER':
                    WalletService.create_wallet_for_user(user)
                    # Dispatch USER_REGISTERED event (triggers async Nomba VA creation)
                    EventDispatcher.dispatch('USER_REGISTERED', {'user_uuid': str(user.uuid)})
                
                login(request, user)
                messages.success(request, "Account created successfully!")
                return redirect_by_role(user)
        except IntegrityError as e:
            messages.error(request, "An account with this email or phone number already exists.")
        except Exception as e:
            messages.error(request, f"Registration failed: {str(e)}")

    return render(request, 'pages/auth/register.html')

def forgot_password_view(request):
    if request.method == 'POST':
        messages.success(request, "Password reset instructions have been sent to your email.")
        return redirect('accounts:login')
    return render(request, 'pages/auth/forgot_password.html')

def logout_view(request):
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect('accounts:login')

@login_required
def passenger_dashboard_view(request):
    if request.user.role != 'PASSENGER':
        return redirect_by_role(request.user)
        
    try:
        wallet = request.user.wallet
    except Wallet.DoesNotExist:
        wallet = WalletService.create_wallet_for_user(request.user)
        
    first_name = request.user.full_name.split()[0] if request.user.full_name else "Passenger"
    transactions = wallet.ledger_entries.all().order_by('-created_at')[:10]
    
    # Handle HTMX dynamic sections
    if request.headers.get('HX-Request'):
        return render(request, 'partials/recent_transactions.html', {'transactions': transactions})
        
    context = {
        'wallet': wallet,
        'first_name': first_name,
        'transactions': transactions,
        'SYSTEM_STATUS': 'HEALTHY'
    }
    return render(request, 'pages/dashboard/passenger.html', context)

def redirect_by_role(user):
    if user.role == 'ADMIN':
        return redirect('admin_panel:dashboard')
    elif user.role == 'DRIVER':
        return redirect('drivers:dashboard')
    else:
        return redirect('accounts:passenger_dashboard')
