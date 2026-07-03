import time
import uuid
import logging
import requests
from decimal import Decimal
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger('payments')


class NombaGatewayError(Exception):
    pass


class NombaAuthenticationError(NombaGatewayError):
    pass


class NombaRateLimitError(NombaGatewayError):
    pass


class NombaCircuitBreakerOpenError(NombaGatewayError):
    pass


class NombaGateway:
    """
    Centralized Nomba API gateway.

    Sandbox mode (NOMBA_ENV=sandbox):
        - No authentication required
        - No accountId header required
        - Calls sandbox endpoints directly
        - No token generation or caching

    Production mode (NOMBA_ENV=production):
        - Client credentials required
        - Bearer token obtained and cached
        - accountId header attached to every request
        - Rate limiting and circuit breaker enforced
    """

    def __init__(self):
        self.env = getattr(settings, 'NOMBA_ENV', 'sandbox')
        self.is_sandbox = (self.env == 'sandbox')

        if self.is_sandbox:
            self.base_url = 'https://sandbox.nomba.com'
            logger.info("NombaGateway: SANDBOX mode — no authentication required.")
        else:
            self.base_url = 'https://api.nomba.com'
            self.client_id = getattr(settings, 'NOMBA_CLIENT_ID', '')
            self.client_secret = getattr(settings, 'NOMBA_CLIENT_SECRET', '')
            self.parent_account_id = getattr(settings, 'NOMBA_PARENT_ACCOUNT_ID', '')
            logger.info(f"NombaGateway: PRODUCTION mode — base URL: {self.base_url}")

    def _get_cache_key(self, key):
        return f"nomba:{self.env}:{key}"

    # --- Rate Limiting (production only) ---

    def _check_rate_limit(self, action_key, limit, window):
        if self.is_sandbox:
            return

        redis_key = f"nomba:rate_limit:{self.env}:{action_key}"
        now = time.time()

        try:
            client = cache.client.get_client()
            pipe = client.pipeline()
            pipe.zremrangebyscore(redis_key, 0, now - window)
            pipe.zcard(redis_key)
            _, count = pipe.execute()

            if count >= limit:
                raise NombaRateLimitError(f"Rate limit exceeded for {action_key}. Allowed: {limit}/{window}s.")

            pipe = client.pipeline()
            pipe.zadd(redis_key, {str(uuid.uuid4()): now})
            pipe.expire(redis_key, window)
            pipe.execute()
        except NombaRateLimitError:
            raise
        except Exception:
            # LocMemCache fallback — simple counter per window
            fallback_key = f"nomba:ratelimit:{self.env}:{action_key}:{int(now / window)}"
            count = cache.get(fallback_key, 0)
            if count >= limit:
                raise NombaRateLimitError(f"Rate limit exceeded for {action_key} (fallback).")
            cache.set(fallback_key, count + 1, timeout=window)

    # --- Circuit Breaker (production only) ---

    def _check_circuit_breaker(self):
        if self.is_sandbox:
            return

        state_key = self._get_cache_key("breaker:state")
        cooldown_end_key = self._get_cache_key("breaker:cooldown_end")

        state = cache.get(state_key, "CLOSED")
        now = time.time()

        if state == "OPEN":
            cooldown_end = cache.get(cooldown_end_key, 0)
            if now > cooldown_end:
                cache.set(state_key, "HALF_OPEN")
                logger.info("Circuit breaker: OPEN → HALF_OPEN")
                canary_lock_key = self._get_cache_key("breaker:canary_lock")
                if not cache.add(canary_lock_key, "active", timeout=15):
                    raise NombaCircuitBreakerOpenError("HALF_OPEN canary already in progress.")
            else:
                remaining = int(cooldown_end - now)
                raise NombaCircuitBreakerOpenError(f"Circuit OPEN. Cooldown: {remaining}s.")
        elif state == "HALF_OPEN":
            canary_lock_key = self._get_cache_key("breaker:canary_lock")
            if not cache.add(canary_lock_key, "active", timeout=15):
                raise NombaCircuitBreakerOpenError("HALF_OPEN canary already in progress.")

    def _handle_request_success(self):
        if self.is_sandbox:
            return

        state_key = self._get_cache_key("breaker:state")
        failures_key = self._get_cache_key("breaker:failure_count")
        canary_lock_key = self._get_cache_key("breaker:canary_lock")

        state = cache.get(state_key, "CLOSED")
        if state == "HALF_OPEN":
            cache.set(state_key, "CLOSED")
            cache.delete(failures_key)
            cache.delete(canary_lock_key)
            logger.info("Circuit breaker reset to CLOSED.")
        else:
            cache.delete(failures_key)

    def _handle_request_failure(self, error_code=None):
        if self.is_sandbox:
            return

        state_key = self._get_cache_key("breaker:state")
        failures_key = self._get_cache_key("breaker:failure_count")
        cooldown_end_key = self._get_cache_key("breaker:cooldown_end")
        canary_lock_key = self._get_cache_key("breaker:canary_lock")

        state = cache.get(state_key, "CLOSED")

        # Trip immediately on Risk Control error 441
        if str(error_code) == "441":
            cooldown_duration = 60
            if state == "HALF_OPEN":
                cooldown_duration = 120

            cache.set(state_key, "OPEN")
            cache.set(cooldown_end_key, time.time() + cooldown_duration, timeout=cooldown_duration)
            cache.delete(canary_lock_key)
            logger.critical(f"Circuit breaker OPEN (risk control 441). Cooldown: {cooldown_duration}s.")
            return

        failures = cache.get(failures_key, 0) + 1
        cache.set(failures_key, failures, timeout=60)

        if failures >= 5 or state == "HALF_OPEN":
            cooldown_duration = 60
            if state == "HALF_OPEN":
                cooldown_duration = 120

            cache.set(state_key, "OPEN")
            cache.set(cooldown_end_key, time.time() + cooldown_duration, timeout=cooldown_duration)
            cache.delete(canary_lock_key)
            cache.set(failures_key, 0)
            logger.error(f"Circuit breaker OPEN. Failures: {failures}. Cooldown: {cooldown_duration}s.")

    # --- Token Management (production only) ---

    def _get_access_token(self):
        if self.is_sandbox:
            return None

        token_state_key = self._get_cache_key("token_state")
        token_lock_key = self._get_cache_key("token_refresh_lock")

        token_state = cache.get(token_state_key)
        now = time.time()

        if token_state and token_state.get('access_token'):
            if token_state.get('expires_at', 0) - now > 300:
                return token_state['access_token']

        # Acquire lock to refresh
        lock_acquired = False
        lock = None
        try:
            lock = cache.lock(token_lock_key, timeout=15)
            lock_acquired = lock.acquire(blocking=False)
        except AttributeError:
            lock_acquired = cache.add(token_lock_key, "locked", timeout=15)

        if lock_acquired:
            try:
                token_state = cache.get(token_state_key)
                if token_state and token_state.get('access_token'):
                    if token_state.get('expires_at', 0) - now > 300:
                        return token_state['access_token']

                self._check_rate_limit("auth", limit=10, window=60)
                access_token, refresh_token, expires_in = self._request_new_token(token_state)

                expires_at = now + expires_in
                new_state = {
                    'access_token': access_token,
                    'refresh_token': refresh_token,
                    'expires_at': expires_at
                }
                cache.set(token_state_key, new_state, timeout=int(expires_in - 300))
                return access_token
            finally:
                if lock and hasattr(lock, 'release'):
                    try:
                        lock.release()
                    except Exception:
                        pass
                else:
                    cache.delete(token_lock_key)
        else:
            for _ in range(30):
                time.sleep(0.5)
                token_state = cache.get(token_state_key)
                if token_state and token_state.get('access_token'):
                    if token_state.get('expires_at', 0) - now > 300:
                        return token_state['access_token']
            raise NombaAuthenticationError("Timeout waiting for token refresh lock.")

    def _request_new_token(self, old_state):
        if old_state and old_state.get('refresh_token'):
            refresh_url = f"{self.base_url}/v1/auth/token/refresh"
            refresh_payload = {
                "grant_type": "refresh_token",
                "refresh_token": old_state['refresh_token']
            }
            try:
                logger.info(f"Refreshing Nomba token: {refresh_url}")
                response = requests.post(
                    refresh_url,
                    json=refresh_payload,
                    headers={"Content-Type": "application/json", "accountId": self.parent_account_id},
                    timeout=10
                )
                res_data = response.json()
                if res_data.get('code') == '00':
                    data = res_data['data']
                    return data['access_token'], data['refresh_token'], data['expiresIn']
            except Exception as e:
                logger.warning(f"Token refresh failed: {e}. Falling back to new token.")

        url = f"{self.base_url}/v1/auth/token/issue"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        logger.info(f"Requesting new Nomba token: {url}")
        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json", "accountId": self.parent_account_id},
            timeout=10
        )

        if response.status_code != 200:
            raise NombaAuthenticationError(f"Token request failed: HTTP {response.status_code}")

        res_data = response.json()
        if res_data.get('code') != '00':
            raise NombaAuthenticationError(f"Token error {res_data.get('code')}: {res_data.get('description')}")

        data = res_data['data']
        return data['access_token'], data['refresh_token'], data['expiresIn']

    # --- Core Request Method ---

    def _request(self, method, endpoint, payload=None, query_params=None, headers=None, rate_limit_config=None):
        if rate_limit_config:
            self._check_rate_limit(*rate_limit_config)

        self._check_circuit_breaker()

        # Build headers based on environment
        req_headers = {"Content-Type": "application/json"}

        if not self.is_sandbox:
            # Production: get token and attach auth headers
            try:
                token = self._get_access_token()
            except NombaAuthenticationError as e:
                self._handle_request_failure()
                raise

            req_headers["Authorization"] = f"Bearer {token}"
            req_headers["accountId"] = self.parent_account_id

        if headers:
            req_headers.update(headers)

        url = f"{self.base_url}{endpoint}"

        try:
            logger.info(f"Nomba {method} {endpoint} ({'sandbox' if self.is_sandbox else 'production'})")
            start_time = time.time()
            response = requests.request(
                method, url, json=payload, params=query_params, headers=req_headers, timeout=15
            )
            latency = time.time() - start_time
            logger.info(f"Nomba Response: {response.status_code} in {latency:.3f}s")
        except requests.RequestException as e:
            logger.error(f"Nomba connection failure for {endpoint}: {e}")
            self._handle_request_failure()
            raise NombaGatewayError(f"HTTP request error: {e}")

        status_code = response.status_code
        try:
            res_data = response.json()
        except ValueError:
            logger.error(f"Non-JSON from Nomba {endpoint}: {response.text[:200]}")
            self._handle_request_failure()
            raise NombaGatewayError(f"Response format error. HTTP: {status_code}")

        code = res_data.get('code')
        description = res_data.get('description', 'No description.')

        if code == '00':
            self._handle_request_success()
            return res_data

        # Error handling
        if code == '441' or str(status_code) == '441':
            self._handle_request_failure(error_code='441')
            raise NombaGatewayError(f"Risk Control Block (441): {description}")

        self._handle_request_failure(error_code=code)

        if status_code == 401:
            raise NombaAuthenticationError(f"Auth failure: {description}")
        elif status_code == 429:
            raise NombaRateLimitError(f"Rate limited: {description}")

        raise NombaGatewayError(f"Nomba error {code}: {description}")

    # --- Public API Methods ---

    def create_checkout_order(self, amount, reference, callback_url, customer_email=None):
        payload = {
            "order": {
                "amount": str(amount),
                "currency": "NGN",
                "callbackUrl": callback_url,
                "orderReference": reference
            }
        }
        if customer_email:
            payload["order"]["customerEmail"] = customer_email

        return self._request(
            "POST",
            "/v1/checkout/order",
            payload=payload,
            rate_limit_config=("checkout", 20, 60)
        )

    def fetch_checkout_transaction(self, identifier, id_type="ORDER_REFERENCE"):
        query_params = {
            "idType": id_type,
            "id": identifier
        }
        return self._request(
            "GET",
            "/v1/checkout/transaction",
            query_params=query_params,
            rate_limit_config=("verification", 30, 60)
        )

    def create_virtual_account(self, account_ref, account_name, bvn=None):
        payload = {
            "accountRef": account_ref,
            "accountName": account_name,
            "currency": "NGN"
        }
        if bvn:
            payload["bvn"] = bvn

        return self._request(
            "POST",
            "/v1/accounts/virtual",
            payload=payload,
            rate_limit_config=("virtual_account", 15, 60)
        )

    def initiate_bank_transfer(self, amount, bank_code, account_number, merchant_tx_ref):
        payload = {
            "amount": str(amount),
            "bankCode": bank_code,
            "accountNumber": account_number,
            "merchantTxRef": merchant_tx_ref
        }
        rate_limit_key = f"transfer:{account_number}"
        return self._request(
            "POST",
            "/v2/transfers/bank",
            payload=payload,
            headers={"X-Idempotent-key": merchant_tx_ref},
            rate_limit_config=(rate_limit_key, 4, 60)
        )

    def get_banks(self):
        return self._request("GET", "/v1/transfers/bank", rate_limit_config=("banks", 30, 60))

    def lookup_account(self, bank_code, account_number):
        payload = {
            "accountNumber": account_number,
            "bankCode": bank_code
        }
        return self._request(
            "POST",
            "/v1/transfers/bank/lookup",
            payload=payload,
            rate_limit_config=("account_lookup", 30, 60)
        )
