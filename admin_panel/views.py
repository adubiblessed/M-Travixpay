from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q
from django.utils import timezone
from datetime import timedelta


# Custom error views
def custom_403(request, exception):
    return render(request, 'pages/errors/403.html', status=403)

def custom_404(request, exception):
    return render(request, 'pages/errors/404.html', status=404)

def custom_500(request):
    return render(request, 'pages/errors/500.html', status=500)


@login_required
def admin_dashboard_view(request):
    if request.user.role != 'ADMIN':
        return redirect('accounts:login')

    from payments.models import PaymentIntent
    from events.models import DomainEvent, WebhookEvent, DeadLetterQueue
    from django.core.cache import cache

    # Payment stats
    today = timezone.now().date()

    success = PaymentIntent.objects.filter(status='SUCCESS')
    pending = PaymentIntent.objects.filter(status__in=['CREATED', 'PROCESSING', 'AWAITING_PAYMENT', 'AWAITING_WEBHOOK'])
    failed = PaymentIntent.objects.filter(status='FAILED')

    success_volume = success.aggregate(total=Sum('amount'))['total'] or 0
    pending_volume = pending.aggregate(total=Sum('amount'))['total'] or 0
    failed_volume = failed.aggregate(total=Sum('amount'))['total'] or 0

    # Circuit breaker state
    breaker_state = cache.get('nomba:sandbox:breaker:state', 'CLOSED')
    if breaker_state == 'CLOSED':
        system_status = 'HEALTHY'
    elif breaker_state == 'HALF_OPEN':
        system_status = 'DEGRADED'
    else:
        system_status = 'CRITICAL'

    # DLQ count
    dlq_count = DeadLetterQueue.objects.count()

    context = {
        'success_volume': success_volume,
        'success_count': success.count(),
        'pending_volume': pending_volume,
        'pending_count': pending.count(),
        'failed_volume': failed_volume,
        'failed_count': failed.count(),
        'SYSTEM_STATUS': system_status,
        'circuit_breaker_state': breaker_state,
        'dlq_count': dlq_count,
    }
    return render(request, 'pages/admin/dashboard.html', context)


@login_required
def audit_logs_view(request):
    if request.user.role != 'ADMIN':
        return redirect('accounts:login')

    from audit.models import AuditLog

    logs = AuditLog.objects.all().order_by('-created_at')

    # Filters
    search_query = request.GET.get('search', '')
    action_filter = request.GET.get('action_filter', '')

    if search_query:
        logs = logs.filter(
            Q(user__email__icontains=search_query) |
            Q(user__full_name__icontains=search_query) |
            Q(action__icontains=search_query)
        )

    if action_filter:
        logs = logs.filter(action=action_filter)

    logs = logs[:100]

    # HTMX partial refresh
    if request.headers.get('HX-Request'):
        return render(request, 'pages/admin/audit_logs.html', {
            'logs': logs,
            'search_query': search_query,
            'action_filter': action_filter,
        })

    context = {
        'logs': logs,
        'search_query': search_query,
        'action_filter': action_filter,
    }
    return render(request, 'pages/admin/audit_logs.html', context)
