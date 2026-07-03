import sys
import logging
from django.conf import settings

logger = logging.getLogger('payments')

class StartupValidation:
    @staticmethod
    def run_checks():
        """
        Validates Nomba API configs.
        Sandbox mode skips credential checks — no auth required.
        """
        skip_commands = {'makemigrations', 'migrate', 'collectstatic', 'test'}
        if any(cmd in sys.argv for cmd in skip_commands):
            logger.info("Skipping startup validation during management task execution.")
            return True

        namba_env = getattr(settings, 'NOMBA_ENV', 'sandbox')

        if namba_env == 'sandbox':
            logger.info("Startup validation: SANDBOX mode — no credentials required. System is HEALTHY.")
            setattr(settings, 'SYSTEM_STATUS', 'HEALTHY')
            setattr(settings, 'SYSTEM_DIAGNOSTICS', [])
            return True

        # Production: validate credentials
        logger.info("Running TravixPay startup validation (production)...")
        errors = []

        client_id = getattr(settings, 'NOMBA_CLIENT_ID', '')
        client_secret = getattr(settings, 'NOMBA_CLIENT_SECRET', '')
        parent_account_id = getattr(settings, 'NOMBA_PARENT_ACCOUNT_ID', '')

        if not client_id or client_id == 'test-client-id':
            errors.append("NOMBA_CLIENT_ID is missing or set to default.")
        if not client_secret or client_secret == 'test-client-secret':
            errors.append("NOMBA_CLIENT_SECRET is missing or set to default.")
        if not parent_account_id or parent_account_id == 'test-parent-account-id':
            errors.append("NOMBA_PARENT_ACCOUNT_ID is missing or set to default.")

        if errors:
            logger.warning("Startup validation FAILED. System entering DEGRADED mode.")
            for error in errors:
                logger.warning(f"  - {error}")
            setattr(settings, 'SYSTEM_STATUS', 'DEGRADED')
            setattr(settings, 'SYSTEM_DIAGNOSTICS', errors)
            return False

        logger.info("Startup validation PASSED. System is HEALTHY.")
        setattr(settings, 'SYSTEM_STATUS', 'HEALTHY')
        setattr(settings, 'SYSTEM_DIAGNOSTICS', [])
        return True
