import uuid
from django.db import models

class DomainEvent(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('PROCESSING', 'Processing'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
    )
    
    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    event_type = models.CharField(max_length=100)
    payload = models.JSONField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    
    def __str__(self):
        return f"Event: {self.event_type} ({self.status}) - {self.event_id}"

class WebhookEvent(models.Model):
    STATUS_CHOICES = (
        ('RECEIVED', 'Received'),
        ('VERIFIED', 'Verified'),
        ('PROCESSED', 'Processed'),
        ('FAILED', 'Failed'),
        ('IGNORED', 'Ignored'),
    )
    
    provider = models.CharField(max_length=50, default='NOMBA')
    event_id = models.CharField(max_length=255, unique=True)  # requestId from Nomba
    event_type = models.CharField(max_length=100)  # e.g., payment_success
    signature = models.CharField(max_length=255)
    payload = models.JSONField()
    processing_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='RECEIVED')
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    
    def __str__(self):
        return f"Webhook: {self.event_type} ({self.processing_status}) - {self.event_id}"

class IdempotencyKey(models.Model):
    key = models.CharField(max_length=255, unique=True)
    resource_type = models.CharField(max_length=100)  # e.g. 'PAYMENT_INTENT', 'FARE_DEDUCTION'
    resource_id = models.CharField(max_length=100, null=True, blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"IdempotencyKey: {self.key} ({self.resource_type})"

class DeadLetterQueue(models.Model):
    event = models.OneToOneField(DomainEvent, on_delete=models.CASCADE, related_name='dlq_record')
    error_reason = models.TextField()
    stack_trace = models.TextField()
    last_attempt_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"DLQ: {self.event.event_type} - {self.event.event_id}"

