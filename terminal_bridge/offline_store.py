"""
TravixPay Terminal Bridge - Offline Storage

SQLite-based store-and-forward for offline taps.
Enforces per-card debt limits.
"""

import sqlite3
import time
import logging
from decimal import Decimal
from typing import List, Tuple, Optional

from terminal_bridge.config import OFFLINE_DB_PATH, OFFLINE_MAX_RIDES, OFFLINE_MAX_DEBT

logger = logging.getLogger("terminal_bridge")


class OfflineStore:
    def __init__(self, db_path: str = OFFLINE_DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS offline_taps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_uid TEXT NOT NULL,
                terminal_id TEXT NOT NULL,
                tap_reference TEXT UNIQUE NOT NULL,
                signature TEXT UNIQUE NOT NULL,
                fare_amount TEXT NOT NULL,
                timestamp REAL NOT NULL,
                status TEXT DEFAULT 'PENDING',
                reconciled_at REAL
            )
        """)
        conn.commit()
        conn.close()

    def check_limits(self, card_uid: str, fare: float) -> Tuple[bool, str]:
        """Check if card is within offline debt limits."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*), COALESCE(SUM(CAST(fare_amount AS REAL)), 0)
            FROM offline_taps
            WHERE card_uid = ? AND status = 'PENDING'
        """, (card_uid,))
        count, total_debt = cursor.fetchone()
        conn.close()

        if count >= OFFLINE_MAX_RIDES:
            return False, f"Offline ride limit reached ({count}/{OFFLINE_MAX_RIDES})"

        if total_debt + fare > OFFLINE_MAX_DEBT:
            return False, f"Offline debt limit reached (₦{total_debt:.2f}/₦{OFFLINE_MAX_DEBT:.2f})"

        return True, "OK"

    def store_tap(self, card_uid: str, terminal_id: str, tap_reference: str,
                  signature: str, fare: float) -> bool:
        """Store a tap for later reconciliation. Returns False if duplicate."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                INSERT INTO offline_taps (card_uid, terminal_id, tap_reference, signature, fare_amount, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (card_uid, terminal_id, tap_reference, signature, str(fare), time.time()))
            conn.commit()
            logger.info(f"Stored offline tap: {tap_reference}")
            return True
        except sqlite3.IntegrityError:
            logger.warning(f"Duplicate offline tap: {tap_reference}")
            return False
        finally:
            conn.close()

    def get_pending(self, limit: int = 10) -> List[dict]:
        """Get pending taps for reconciliation."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, card_uid, terminal_id, tap_reference, fare_amount, timestamp
            FROM offline_taps
            WHERE status = 'PENDING'
            ORDER BY timestamp ASC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def mark_reconciled(self, ids: List[int]):
        """Mark taps as reconciled."""
        if not ids:
            return
        conn = sqlite3.connect(self.db_path)
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"""
            UPDATE offline_taps
            SET status = 'RECONCILED', reconciled_at = ?
            WHERE id IN ({placeholders})
        """, [time.time()] + ids)
        conn.commit()
        conn.close()
        logger.info(f"Marked {len(ids)} taps as reconciled")

    def mark_failed(self, tap_id: int):
        """Mark a tap as failed reconciliation."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            UPDATE offline_taps SET status = 'FAILED' WHERE id = ?
        """, (tap_id,))
        conn.commit()
        conn.close()

    def get_stats(self) -> dict:
        """Get offline store statistics."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM offline_taps WHERE status = 'PENDING'")
        pending = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM offline_taps WHERE status = 'RECONCILED'")
        reconciled = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM offline_taps WHERE status = 'FAILED'")
        failed = cursor.fetchone()[0]

        conn.close()
        return {"pending": pending, "reconciled": reconciled, "failed": failed}
