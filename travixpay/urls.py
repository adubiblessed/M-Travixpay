"""
URL configuration for travixpay project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include

# Custom error handlers
handler403 = 'admin_panel.views.custom_403'
handler404 = 'admin_panel.views.custom_404'
handler500 = 'admin_panel.views.custom_500'

urlpatterns = [
    path('admin-django/', admin.site.urls),

    # TravixPay URL modules
    path('', include('accounts.urls')),
    path('', include('payments.urls')),
    path('', include('wallets.urls')),
    path('', include('cards.urls')),
    path('', include('drivers.urls')),
    path('', include('admin_panel.urls')),
    path('', include('terminals.urls')),
]
