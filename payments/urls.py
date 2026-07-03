from django.urls import path
from payments import views

app_name = 'payments'

urlpatterns = [
    # API Webhook
    path('payments/webhook/', views.nomba_webhook_view, name='nomba_webhook'),
    
    # Funding Checkout Flow
    path('wallet/fund/', views.fund_start_view, name='fund_start'),
    path('wallet/fund/review/', views.fund_review_view, name='fund_review'),
    path('wallet/fund/loading/<uuid:intent_uuid>/', views.fund_loading_view, name='fund_loading'),
    path('wallet/fund/poll/<uuid:intent_uuid>/', views.fund_poll_view, name='fund_poll'),
    path('wallet/fund/success/', views.fund_success_view, name='fund_success'),
    path('wallet/fund/failure/', views.fund_failure_view, name='fund_failure'),
    
    # Callback Redirection URL
    path('payments/callback/', views.payment_callback_view, name='payment_callback'),
]
