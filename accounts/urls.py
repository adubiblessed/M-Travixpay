from django.urls import path
from accounts import views

app_name = 'accounts'

urlpatterns = [
    path('auth/login/', views.login_view, name='login'),
    path('auth/register/', views.register_view, name='register'),
    path('auth/forgot-password/', views.forgot_password_view, name='forgot_password'),
    path('auth/logout/', views.logout_view, name='logout'),
    path('dashboard/', views.passenger_dashboard_view, name='passenger_dashboard'),
]
