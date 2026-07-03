from django.urls import path
from cards import views

app_name = 'cards'

urlpatterns = [
    path('cards/', views.manage_view, name='manage'),
]
