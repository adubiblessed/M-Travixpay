import uuid
from decimal import Decimal
from django.db import models
from django.db.models import Sum
from django.conf import settings

class Wallet(models.Model):
    STATUS_CHOICES = (
        ('ACTIVE', 'Active'),
        ('LOCKED', 'Locked'),
        ('SUSPENDED', 'Suspended'),
        ('CLOSED', 'Closed'),
        ('PENDING_EXTERNAL_SETUP', 'Pending External Setup'),
    )
    
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='wallet')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='PENDING_EXTERNAL_SETUP')
    currency = models.CharField(max_length=10, default='NGN')
    virtual_account_number = models.CharField(max_length=50, blank=True, null=True)
    virtual_account_name = models.CharField(max_length=255, blank=True, null=True)
    virtual_account_provider = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Wallet: {self.user.full_name} ({self.status})"
    
    @property
    def balance(self):
        # We calculate the balance by summing up all CREDIT, REFUND, and ADJUSTMENT (if positive) entries, 
        # and subtracting all DEBIT, REVERSAL, and ADJUSTMENT (if negative) entries.
        # However, to keep it simple, we treat ADJUSTMENT as positive (credit) or negative (debit) based on its sign.
        
        # Positive entries (Additions)
        additions = self.ledger_entries.filter(
            entry_type__in=['CREDIT', 'REFUND']
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        # Negative entries (Subtractions)
        subtractions = self.ledger_entries.filter(
            entry_type__in=['DEBIT', 'REVERSAL']
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        # Handle adjustments
        adjustments = self.ledger_entries.filter(
            entry_type='ADJUSTMENT'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        return additions - subtractions + adjustments

class WalletLedger(models.Model):
    ENTRY_TYPE_CHOICES = (
        ('CREDIT', 'Credit'),
        ('DEBIT', 'Debit'),
        ('REVERSAL', 'Reversal'),
        ('REFUND', 'Refund'),
        ('ADJUSTMENT', 'Adjustment'),
    )
    
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='ledger_entries')
    reference = models.CharField(max_length=255, unique=True)
    entry_type = models.CharField(max_length=20, choices=ENTRY_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField()
    source = models.CharField(max_length=100)  # e.g., 'PAYMENT', 'FARE_DEDUCTION', 'REFUND'
    source_id = models.CharField(max_length=100)  # ID of the source transaction
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.entry_type} | {self.reference} | ₦{self.amount}"
