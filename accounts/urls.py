from django.urls import path
from accounts import views

app_name = 'accounts'

urlpatterns = [
    path('accounts/login/', views.login_view, name='login'),
    path('accounts/register/', views.register_view, name='register'),
    path('accounts/forgot-password/', views.forgot_password_view, name='forgot_password'),
    path('accounts/logout/', views.logout_view, name='logout'),
    path('dashboard/', views.passenger_dashboard_view, name='passenger_dashboard'),
    path('', views.passenger_dashboard_view, name='passenger_dashboard'),
]
