import uuid
from django.db import models
from django.conf import settings

class PaymentIntent(models.Model):
    STATUS_CHOICES = (
        ('CREATED', 'Created'),
        ('PROCESSING', 'Processing'),
        ('AWAITING_PAYMENT', 'Awaiting Payment'),
        ('AWAITING_WEBHOOK', 'Awaiting Webhook'),
        ('SUCCESS', 'Success'),
        ('FAILED', 'Failed'),
        ('EXPIRED', 'Expired'),
        ('CANCELLED', 'Cancelled'),
    )
    
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='payment_intents')
    wallet = models.ForeignKey('wallets.Wallet', on_delete=models.PROTECT, related_name='payment_intents')
    reference = models.CharField(max_length=255, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default='NGN')
    provider = models.CharField(max_length=50, default='NOMBA')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='CREATED')
    checkout_url = models.URLField(max_length=500, blank=True, null=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"PaymentIntent: {self.reference} - ₦{self.amount} ({self.status})"

class PaymentTransaction(models.Model):
    STATUS_CHOICES = (
        ('SUCCESS', 'Success'),
        ('FAILED', 'Failed'),
        ('PENDING', 'Pending'),
        ('REFUNDED', 'Refunded'),
    )
    
    payment_intent = models.ForeignKey(PaymentIntent, on_delete=models.PROTECT, related_name='transactions')
    provider_reference = models.CharField(max_length=255, unique=True)
    provider_transaction_id = models.CharField(max_length=255, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=50, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    raw_response = models.TextField(blank=True, null=True)
    processed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"PaymentTx: {self.provider_reference} - {self.status}"

class VirtualAccount(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('ACTIVE', 'Active'),
        ('FAILED', 'Failed'),
        ('DISABLED', 'Disabled'),
    )
    
    wallet = models.ForeignKey('wallets.Wallet', on_delete=models.PROTECT, related_name='virtual_accounts')
    provider = models.CharField(max_length=50, default='NOMBA')
    account_number = models.CharField(max_length=50)
    account_name = models.CharField(max_length=255)
    provider_customer_id = models.CharField(max_length=100, blank=True, null=True)
    provider_account_id = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"VA: {self.account_number} ({self.account_name})"
