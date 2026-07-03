"""
TravixPay Terminal Bridge - Configuration
"""

# Serial settings
SERIAL_PORT = "/dev/ttyUSB0"
SERIAL_BAUD = 9600
SERIAL_TIMEOUT = 1  # seconds

# Backend API
BACKEND_URL = "http://127.0.0.1:8000"
TAP_ENDPOINT = "/terminals/tap/"

# Terminal identity
TERMINAL_ID = "TRM-DEFAULT"

# Offline limits
OFFLINE_MAX_RIDES = 2
OFFLINE_MAX_DEBT = 1000.00  # NGN

# Reconciliation
RECONCILE_INTERVAL = 30  # seconds between sync attempts
RECONCILE_BATCH_SIZE = 10

# Paths
OFFLINE_DB_PATH = "offline_taps.db"
BRIDGE_LOG_PATH = "terminal_bridge.log"
