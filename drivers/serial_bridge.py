import time
import json
import uuid
import sqlite3
import logging
import hashlib
import requests
from decimal import Decimal

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("serial_bridge.log")
    ]
)
logger = logging.getLogger("serial_bridge")

# Try to import serial (pyserial)
try:
    import serial
except ImportError:
    logger.warning("pyserial is not installed. Running in emulation mode.")
    serial = None

class SerialBridge:
    def __init__(self, port="/dev/ttyUSB0", baudrate=9600, backend_url="http://localhost:8000", terminal_code="TERM-001", fare_amount=200.00):
        self.port = port
        self.baudrate = baudrate
        self.backend_url = backend_url.rstrip("/")
        self.terminal_code = terminal_code
        self.fare_amount = Decimal(str(fare_amount))
        self.db_path = "offline_taps.db"
        
        self.setup_local_database()
        
    def setup_local_database(self):
        """
        Initializes local SQLite DB to persist offline taps for store-and-forward.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS offline_taps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_uid TEXT NOT NULL,
                terminal_code TEXT NOT NULL,
                tap_reference TEXT UNIQUE NOT NULL,
                signature TEXT UNIQUE NOT NULL,
                fare_amount TEXT NOT NULL,
                timestamp REAL NOT NULL,
                status TEXT DEFAULT 'PENDING_RECONCILIATION',
                reconciled_at REAL
            )
        """)
        conn.commit()
        conn.close()

    def generate_signature(self, card_uid, timestamp):
        """
        Generates a unique, deterministic SHA-256 signature to prevent duplicate taps.
        """
        raw_string = f"{card_uid}:{self.terminal_code}:{timestamp}"
        return hashlib.sha256(raw_string.encode('utf-8')).hexdigest()

    def check_offline_limits(self, card_uid):
        """
        Enforces the offline tap rules:
        - Max 2 offline rides.
        - Max ₦1,000 cumulative offline debt.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get count and sum of pending offline taps for this card
        cursor.execute("""
            SELECT COUNT(*), SUM(CAST(fare_amount AS REAL)) 
            FROM offline_taps 
            WHERE card_uid = ? AND status = 'PENDING_RECONCILIATION'
        """, (card_uid,))
        
        count, total_debt = cursor.fetchone()
        conn.close()
        
        count = count or 0
        total_debt = total_debt or 0.0
        
        # Threshold checks
        max_rides = 2
        max_debt = 1000.00
        
        if count >= max_rides:
            logger.warning(f"Card {card_uid} rejected: Offline ride count limit reached ({count}/{max_rides})")
            return False, f"Offline limit reached ({count} rides)"
            
        if total_debt + float(self.fare_amount) > max_debt:
            logger.warning(f"Card {card_uid} rejected: Cumulative offline debt limit reached (₦{total_debt}/₦{max_debt})")
            return False, f"Offline debt limit reached (₦{total_debt:.2f})"
            
        return True, "Within offline limits"

    def record_offline_tap(self, card_uid, tap_ref, signature, timestamp):
        """
        Saves the tap locally as pending reconciliation.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO offline_taps (card_uid, terminal_code, tap_reference, signature, fare_amount, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (card_uid, self.terminal_code, tap_ref, signature, str(self.fare_amount), timestamp))
            conn.commit()
            logger.info(f"Recorded offline tap for {card_uid} [Ref: {tap_ref}]")
            return True
        except sqlite3.IntegrityError:
            logger.warning(f"Duplicate tap signature detected: {signature}")
            return False
        finally:
            conn.close()

    def process_tap(self, card_uid):
        """
        Decides whether to process tap online or buffer offline.
        """
        tap_ref = f"tap-{uuid.uuid4()}"
        timestamp = time.time()
        signature = self.generate_signature(card_uid, timestamp)
        
        # Try online first
        url = f"{self.backend_url}/terminals/tap/"
        payload = {
            "card_uid": card_uid,
            "terminal_code": self.terminal_code,
            "tap_reference": tap_ref,
            "fare_amount": str(self.fare_amount)
        }
        
        try:
            logger.info(f"Sending tap request online: {card_uid}")
            response = requests.post(url, json=payload, timeout=3)
            
            if response.status_code == 200:
                res_data = response.json()
                status = res_data.get("status")
                reason = res_data.get("reason", "Success")
                logger.info(f"Online Response: {status} - {reason}")
                return status, reason
            else:
                logger.warning(f"Backend returned status code {response.status_code}. Falling back to offline.")
        except (requests.RequestException, ValueError) as e:
            logger.warning(f"Backend unreachable ({e}). Falling back to offline.")
            
        # Offline Flow
        allowed, message = self.check_offline_limits(card_uid)
        if not allowed:
            return "DECLINED", message
            
        success = self.record_offline_tap(card_uid, tap_ref, signature, timestamp)
        if success:
            return "APPROVED", "Approved Offline"
        else:
            return "DECLINED", "Duplicate Tap detected"

    def reconcile_offline_taps(self):
        """
        Store-and-forward loop that syncs offline taps to the backend when online.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, card_uid, tap_reference, fare_amount, timestamp FROM offline_taps WHERE status = 'PENDING_RECONCILIATION'")
        pending = cursor.fetchall()
        conn.close()
        
        if not pending:
            return
            
        logger.info(f"Attempting to reconcile {len(pending)} pending offline taps...")
        url = f"{self.backend_url}/terminals/tap/"
        
        reconciled_ids = []
        
        for record in pending:
            db_id, card_uid, tap_ref, fare_amount, timestamp = record
            payload = {
                "card_uid": card_uid,
                "terminal_code": self.terminal_code,
                "tap_reference": tap_ref,
                "fare_amount": fare_amount
            }
            try:
                # Deduplication handled at Django view via tap_reference
                response = requests.post(url, json=payload, timeout=5)
                if response.status_code == 200:
                    reconciled_ids.append(db_id)
                    logger.info(f"Successfully reconciled tap {tap_ref}")
                else:
                    logger.error(f"Reconciliation failed for tap {tap_ref}: HTTP {response.status_code}")
            except requests.RequestException as e:
                logger.warning(f"Reconciliation paused. Backend still offline: {e}")
                break
                
        if reconciled_ids:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            placeholders = ",".join("?" for _ in reconciled_ids)
            cursor.execute(f"""
                UPDATE offline_taps 
                SET status = 'RECONCILED', reconciled_at = ? 
                WHERE id IN ({placeholders})
            """, [time.time()] + reconciled_ids)
            conn.commit()
            conn.close()
            logger.info(f"Reconciliation batch finished. Successfully updated {len(reconciled_ids)} records.")

    def run(self):
        """
        Main loop listening to the serial port.
        """
        logger.info(f"Serial Bridge active. Port: {self.port} | Backend: {self.backend_url}")
        
        if not serial:
            logger.warning("Emulation mode active. Type RFID card UIDs to simulate taps.")
            
        # Last reconciliation run timestamp
        last_reconcile = 0
        
        ser = None
        if serial:
            try:
                ser = serial.Serial(self.port, self.baudrate, timeout=1)
            except Exception as e:
                logger.error(f"Failed to open serial port {self.port}: {e}. Falling back to emulation.")
                ser = None
                
        while True:
            try:
                # Periodically trigger reconciliation (every 30 seconds)
                now = time.time()
                if now - last_reconcile > 30:
                    self.reconcile_offline_taps()
                    last_reconcile = now
                    
                # Read RFID UID
                card_uid = None
                if ser:
                    if ser.in_waiting > 0:
                        card_uid = ser.readline().decode('utf-8').strip()
                else:
                    # Emulated console input (non-blocking simulation check)
                    import select
                    import sys
                    if select.select([sys.stdin], [], [], 0.1)[0]:
                        card_uid = sys.stdin.readline().strip()
                        
                if card_uid:
                    logger.info(f"Read card: {card_uid}")
                    status, reason = self.process_tap(card_uid)
                    
                    # Write status back to serial
                    response_msg = f"{status}:{reason}\n"
                    if ser:
                        ser.write(response_msg.encode('utf-8'))
                        logger.info(f"Wrote to Arduino serial: {status}:{reason}")
                    else:
                        print(f"[Emulated Serial Output] -> {status}:{reason}")
                        
            except KeyboardInterrupt:
                logger.info("Serial Bridge shutting down.")
                if ser:
                    ser.close()
                break
            except Exception as e:
                logger.error(f"Error in serial bridge loop: {e}")
                time.sleep(1)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="TravixPay Python Serial Bridge")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="Serial port connection")
    parser.add_argument("--backend", default="http://localhost:8000", help="TravixPay Django URL")
    parser.add_argument("--terminal", default="TERM-001", help="Terminal code")
    parser.add_argument("--fare", default=200.0, type=float, help="Default transit fare amount")
    args = parser.parse_args()
    
    bridge = SerialBridge(
        port=args.port,
        backend_url=args.backend,
        terminal_code=args.terminal,
        fare_amount=args.fare
    )
    bridge.run()
