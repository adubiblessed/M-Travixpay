from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'payments'

    def ready(self):
        # Delayed import to ensure models and settings are loaded
        from payments.services.startup_validation import StartupValidation
        StartupValidation.run_checks()

