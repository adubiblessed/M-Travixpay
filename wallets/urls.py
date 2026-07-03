from django.urls import path
from wallets import views

app_name = 'wallets'

urlpatterns = [
    path('wallet/virtual-account/', views.virtual_account_detail, name='virtual_account_detail'),
    path('wallet/virtual-account/create/', views.create_virtual_account, name='create_virtual_account'),
    path('wallet/balance/partials/', views.balance_partial, name='balance_partial'),
]
