"""
TravixPay Terminal Bridge - Django API Client

Communicates with Django backend for card tap processing.
No Nomba calls — all Nomba interaction happens in Django.
"""

import time
import logging
import requests
from typing import Optional

from terminal_bridge.config import BACKEND_URL, TAP_ENDPOINT

logger = logging.getLogger("terminal_bridge")


class ApiClient:
    def __init__(self, base_url: str = BACKEND_URL, timeout: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.is_online = True

    def send_tap(self, card_uid: str, terminal_id: str,
                 tap_reference: str, fare_amount: float = None) -> Optional[dict]:
        """
        Send card tap to Django backend.
        If fare_amount is None, backend resolves it from destination FareRule.
        Returns response dict or None if offline/error.
        """
        url = f"{self.base_url}{TAP_ENDPOINT}"
        payload = {
            "card_uid": card_uid,
            "terminal_code": terminal_id,
            "tap_reference": tap_reference,
        }
        if fare_amount is not None:
            payload["fare_amount"] = str(fare_amount)

        try:
            start = time.time()
            resp = self.session.post(url, json=payload, timeout=self.timeout)
            latency = time.time() - start
            logger.info(f"API response: {resp.status_code} in {latency:.3f}s")

            self.is_online = True

            if resp.status_code == 200:
                return resp.json()
            else:
                logger.warning(f"API error: {resp.status_code} {resp.text[:200]}")
                return {"status": "ERROR", "reason": f"HTTP {resp.status_code}"}

        except requests.ConnectionError:
            logger.warning("Backend unreachable — entering offline mode")
            self.is_online = False
            return None

        except requests.Timeout:
            logger.warning("Backend timeout")
            return {"status": "ERROR", "reason": "TIMEOUT"}

        except Exception as e:
            logger.error(f"API error: {e}")
            return None

    def check_health(self) -> bool:
        """Check if backend is reachable."""
        try:
            resp = self.session.get(
                f"{self.base_url}/",
                timeout=2
            )
            self.is_online = True
            return True
        except Exception:
            self.is_online = False
            return False
