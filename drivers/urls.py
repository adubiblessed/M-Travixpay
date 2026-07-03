from django.urls import path
from drivers import views

app_name = 'drivers'

urlpatterns = [
    path('driver/dashboard/', views.driver_dashboard_view, name='dashboard'),
    path('driver/destination/create/', views.create_destination, name='create_destination'),
    path('driver/destination/<int:terminal_id>/', views.destination_detail, name='destination_detail'),
    path('driver/stats/partial/', views.driver_stats_partial, name='stats_partial'),
]
