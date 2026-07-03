from django.urls import path
from terminals import views

urlpatterns = [
    path('terminals/tap/', views.process_terminal_tap_view, name='terminal_tap'),
]
