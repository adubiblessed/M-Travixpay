import uuid
from django.db import models
from django.conf import settings

class Terminal(models.Model):
    STATUS_CHOICES = (
        ('ONLINE', 'Online'),
        ('OFFLINE', 'Offline'),
        ('MAINTENANCE', 'Maintenance'),
        ('DISABLED', 'Disabled'),
    )
    
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=255)
    terminal_code = models.CharField(max_length=50, unique=True)
    driver = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='terminals'
    )
    vehicle_number = models.CharField(max_length=50)
    route = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='OFFLINE')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.name} ({self.terminal_code})"

class FareRule(models.Model):
    route_name = models.CharField(max_length=255, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Fare for {self.route_name}: ₦{self.amount}"

class FareTransaction(models.Model):
    STATUS_CHOICES = (
        ('SUCCESS', 'Success'),
        ('FAILED', 'Failed'),
        ('REVERSED', 'Reversed'),
    )
    
    wallet = models.ForeignKey('wallets.Wallet', on_delete=models.PROTECT, related_name='fare_transactions')
    card = models.ForeignKey('cards.RFIDCard', on_delete=models.PROTECT, related_name='fare_transactions')
    terminal = models.ForeignKey(Terminal, on_delete=models.PROTECT, related_name='fare_transactions')
    reference = models.CharField(max_length=255, unique=True)
    fare_amount = models.DecimalField(max_digits=10, decimal_places=2)
    ledger_entry = models.OneToOneField(
        'wallets.WalletLedger', 
        on_delete=models.PROTECT, 
        null=True, 
        blank=True, 
        related_name='fare_transaction'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='SUCCESS')
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"FareTx: {self.reference} - ₦{self.fare_amount}"
