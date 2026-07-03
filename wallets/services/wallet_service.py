import logging
from django.db import transaction
from accounts.models import User
from wallets.models import Wallet, WalletLedger
from payments.models import VirtualAccount
from payments.services.nomba_gateway import NombaGateway

logger = logging.getLogger('payments')

class WalletService:
    @staticmethod
    @transaction.atomic
    def create_wallet_for_user(user):
        """
        Creates a local wallet for the registered user in PENDING_EXTERNAL_SETUP status.
        """
        wallet, created = Wallet.objects.get_or_create(
            user=user,
            defaults={
                'status': 'PENDING_EXTERNAL_SETUP',
                'currency': 'NGN'
            }
        )
        if created:
            logger.info(f"Local wallet created for user: {user.email}")
        return wallet

    @staticmethod
    def provision_virtual_account(user_uuid):
        """
        Asynchronously calls Nomba API to provision a virtual account and 
        updates the local wallet status to ACTIVE.
        
        Designed to be executed in Celery background task.
        """
        try:
            user = User.objects.get(uuid=user_uuid)
        except User.DoesNotExist:
            logger.error(f"User with UUID {user_uuid} not found for virtual account provisioning.")
            return
            
        # Select wallet using select_for_update to handle concurrency
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(user=user)
            
            # If already active, skip
            if wallet.status == 'ACTIVE' and wallet.virtual_account_number:
                logger.info(f"Wallet is already active with VA: {wallet.virtual_account_number}")
                return
                
        gateway = NombaGateway()
        
        # Unique reference and name for virtual account
        account_ref = f"VA-{wallet.uuid}"
        account_name = f"TravixPay - {user.full_name}"
        
        try:
            logger.info(f"Provisioning Nomba Virtual Account for wallet {wallet.uuid}")
            res = gateway.create_virtual_account(account_ref, account_name)
            data = res['data']
            
            # Save virtual account details and activate wallet
            with transaction.atomic():
                wallet = Wallet.objects.select_for_update().get(id=wallet.id)
                
                VirtualAccount.objects.update_or_create(
                    wallet=wallet,
                    provider='NOMBA',
                    defaults={
                        'account_number': data['bankAccountNumber'],
                        'account_name': data['bankAccountName'],
                        'provider_account_id': data.get('accountHolderId', ''),
                        'status': 'ACTIVE'
                    }
                )
                
                wallet.virtual_account_number = data['bankAccountNumber']
                wallet.virtual_account_name = data['bankAccountName']
                wallet.virtual_account_provider = data.get('bankName', 'Nomba Provider')
                wallet.status = 'ACTIVE'
                wallet.save(update_fields=[
                    'virtual_account_number', 
                    'virtual_account_name', 
                    'virtual_account_provider', 
                    'status'
                ])
                
            logger.info(f"Successfully provisioned VA: {data['bankAccountNumber']} for Wallet: {wallet.uuid}")
            
        except Exception as e:
            logger.error(f"Failed to provision virtual account for user {user.email}: {e}")
            # Retain PENDING_EXTERNAL_SETUP so background scheduler retries
            with transaction.atomic():
                wallet = Wallet.objects.select_for_update().get(id=wallet.id)
                wallet.status = 'PENDING_EXTERNAL_SETUP'
                wallet.save(update_fields=['status'])
            # Re-raise the exception to Celery so it retries or logs to DLQ
            raise e
