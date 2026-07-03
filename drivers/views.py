import uuid
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, Count
from django.utils import timezone
from datetime import timedelta

from terminals.models import Terminal, FareRule, FareTransaction
from cards.models import CardTapLog

logger = logging.getLogger('payments')


@login_required
def driver_dashboard_view(request):
    if request.user.role != 'DRIVER':
        return redirect('accounts:login')

    terminals = Terminal.objects.filter(driver=request.user)

    # Aggregate stats across all driver's terminals
    today = timezone.now().date()
    today_taps = CardTapLog.objects.filter(
        terminal__in=terminals,
        created_at__date=today
    )
    taps_count = today_taps.count()
    recent_taps = today_taps.order_by('-created_at')[:15]

    total_revenue = FareTransaction.objects.filter(
        terminal__in=terminals,
        created_at__date=today,
        status='SUCCESS'
    ).aggregate(total=Sum('fare_amount'))['total'] or 0

    # Per-destination stats
    destinations = []
    for terminal in terminals:
        fare_rule = FareRule.objects.filter(route_name=terminal.route, is_active=True).first()
        fare = fare_rule.amount if fare_rule else 0
        collected = FareTransaction.objects.filter(
            terminal=terminal,
            status='SUCCESS'
        ).aggregate(total=Sum('fare_amount'))['total'] or 0
        tap_count = CardTapLog.objects.filter(terminal=terminal).count()
        destinations.append({
            'terminal': terminal,
            'fare': fare,
            'total_collected': collected,
            'tap_count': tap_count,
        })

    context = {
        'terminals': terminals,
        'destinations': destinations,
        'total_revenue': total_revenue,
        'taps_count': taps_count,
        'recent_taps': recent_taps,
    }
    return render(request, 'pages/driver/dashboard.html', context)


@login_required
def create_destination(request):
    """Driver creates a new destination (terminal + route + fare)."""
    if request.user.role != 'DRIVER':
        return redirect('accounts:login')

    if request.method == 'POST':
        route_name = request.POST.get('route_name', '').strip()
        fare_amount = request.POST.get('fare_amount', '').strip()
        vehicle_number = request.POST.get('vehicle_number', '').strip()

        if not route_name or not fare_amount:
            messages.error(request, "Route name and fare amount are required.")
            return redirect('drivers:dashboard')

        try:
            from decimal import Decimal, InvalidOperation
            fare = Decimal(fare_amount)
            if fare <= 0:
                raise ValueError("Fare must be positive")
        except (InvalidOperation, ValueError):
            messages.error(request, "Enter a valid fare amount.")
            return redirect('drivers:dashboard')

        # Generate unique terminal code
        terminal_code = f"TRM-{uuid.uuid4().hex[:8].upper()}"

        # Create terminal
        terminal = Terminal.objects.create(
            name=f"{route_name} Terminal",
            terminal_code=terminal_code,
            driver=request.user,
            vehicle_number=vehicle_number or 'N/A',
            route=route_name,
            status='ONLINE'
        )

        # Create fare rule for this route
        FareRule.objects.get_or_create(
            route_name=route_name,
            defaults={'amount': fare, 'is_active': True}
        )

        messages.success(
            request,
            f"Destination '{route_name}' created! Terminal code: {terminal_code} | Fare: ₦{fare}"
        )
        logger.info(f"Driver {request.user.email} created destination: {route_name} ({terminal_code})")

    return redirect('drivers:dashboard')


@login_required
def destination_detail(request, terminal_id):
    """View details and passenger payment history for a specific destination."""
    if request.user.role != 'DRIVER':
        return redirect('accounts:login')

    terminal = get_object_or_404(Terminal, id=terminal_id, driver=request.user)
    fare_rule = FareRule.objects.filter(route_name=terminal.route, is_active=True).first()

    # All successful fare transactions for this destination
    transactions = FareTransaction.objects.filter(
        terminal=terminal,
        status='SUCCESS'
    ).order_by('-created_at')[:50]

    total_collected = FareTransaction.objects.filter(
        terminal=terminal,
        status='SUCCESS'
    ).aggregate(total=Sum('fare_amount'))['total'] or 0

    total_passengers = FareTransaction.objects.filter(
        terminal=terminal,
        status='SUCCESS'
    ).values('wallet').distinct().count()

    total_taps = CardTapLog.objects.filter(terminal=terminal).count()

    context = {
        'terminal': terminal,
        'fare_rule': fare_rule,
        'transactions': transactions,
        'total_collected': total_collected,
        'total_passengers': total_passengers,
        'total_taps': total_taps,
    }
    return render(request, 'pages/driver/destination_detail.html', context)


@login_required
def driver_stats_partial(request):
    if request.user.role != 'DRIVER':
        return redirect('accounts:login')

    terminals = Terminal.objects.filter(driver=request.user)
    recent_taps = CardTapLog.objects.filter(
        terminal__in=terminals
    ).order_by('-created_at')[:20]

    return render(request, 'partials/driver_stats.html', {
        'recent_taps': recent_taps,
    })
