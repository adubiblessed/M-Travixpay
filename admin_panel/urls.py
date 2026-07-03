from django.urls import path
from admin_panel import views

app_name = 'admin_panel'

urlpatterns = [
    path('admin/dashboard/', views.admin_dashboard_view, name='dashboard'),
    path('admin/audit-logs/', views.audit_logs_view, name='audit_logs'),
]
