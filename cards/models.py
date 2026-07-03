import uuid
from django.db import models
from django.conf import settings

class RFIDCard(models.Model):
    STATUS_CHOICES = (
        ('ACTIVE', 'Active'),
        ('BLOCKED', 'Blocked'),
        ('LOST', 'Lost'),
        ('EXPIRED', 'Expired'),
    )
    
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='rfid_cards')
    card_uid = models.CharField(max_length=50, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE')
    linked_at = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"RFID Card: {self.card_uid} ({self.status})"

class CardTapLog(models.Model):
    STATUS_CHOICES = (
        ('APPROVED', 'Approved'),
        ('DECLINED', 'Declined'),
        ('ERROR', 'Error'),
    )
    
    card = models.ForeignKey(RFIDCard, on_delete=models.SET_NULL, null=True, blank=True, related_name='tap_logs')
    terminal = models.ForeignKey('terminals.Terminal', on_delete=models.PROTECT, related_name='tap_logs')
    tap_reference = models.CharField(max_length=255, unique=True)
    fare_amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    response_message = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Tap: {self.tap_reference} | {self.status}"
