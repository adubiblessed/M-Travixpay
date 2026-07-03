---
name: Nombainc
description: Use when building payment acceptance flows, processing transfers, managing virtual accounts, or integrating Nomba's payment infrastructure into applications. Reach for this skill when implementing checkout, webhooks, authentication, or transaction verification.
metadata:
    mintlify-proj: nombainc
    version: "1.0"
---

# Nomba API Skill

## Product summary

Nomba is a payment infrastructure API for accepting payments (checkout, virtual accounts, direct debit), processing bank transfers, managing terminals, and handling bill payments and airtime vending. Agents use it to build payment flows, verify transactions, set up webhooks, and manage accounts. The primary documentation is at https://developer.nomba.com. Key files: API credentials from the dashboard, OAuth 2.0 tokens, webhook signature keys. Base URLs: `https://api.nomba.com` (production) and `https://sandbox.nomba.com` (sandbox). All responses follow a standard structure with a `code` field (always check it—`"00"` means success).

## When to use

Reach for this skill when:
- Building a payment acceptance flow (checkout, virtual accounts, or direct debit)
- Processing bank transfers to external accounts
- Setting up webhooks to receive payment notifications
- Verifying transaction status before delivering goods or services
- Managing sub-accounts or virtual accounts
- Testing integrations in sandbox before going live
- Handling authentication, token refresh, or error responses
- Implementing idempotency for transfer requests
- Troubleshooting webhook signature verification or rate limit issues

## Quick reference

### Authentication flow
1. Exchange `client_id` and `client_secret` for `access_token` at `/v1/auth/token/issue`
2. Access tokens expire after 30 minutes—refresh using `refresh_token` at `/v1/auth/token/refresh`
3. Include `Authorization: Bearer <token>` and `accountId` headers in all requests
4. Always pair sandbox credentials with `https://sandbox.nomba.com`; production credentials with `https://api.nomba.com`

### Response structure
```json
{
  "code": "00",
  "description": "Success",
  "data": { ... }
}
```
**Always check the `code` field.** A `200 HTTP` status does not guarantee success—only `code: "00"` indicates success.

### Key endpoints by product

| Product | Endpoint | Purpose |
|---------|----------|---------|
| **Checkout** | `POST /v1/checkout/order` | Create a payment link |
| **Checkout** | `GET /v1/transactions/accounts/single` | Verify transaction status |
| **Virtual Accounts** | `POST /v1/accounts/virtual` | Create a virtual account |
| **Transfers** | `POST /v2/transfers/bank` | Send money to a bank account |
| **Transfers** | `POST /v1/transfers/bank/lookup` | Verify account name before transfer |
| **Transfers** | `GET /v1/transfers/bank` | Fetch list of banks and codes |
| **Webhooks** | Dashboard → Developer → Webhook Setup | Configure webhook URL and events |

### Sandbox testing without credentials
Test Transfer, Virtual Account, and Checkout endpoints directly at `https://sandbox.nomba.com` without authentication headers or `accountId`. Useful for quick exploration.

### Rate limits (default)
- **Regular accounts:** 15 POST requests per second, 75 total requests per second
- **Transfer-specific:** 5 bank transfers to the same recipient per minute
- Response headers include `X-Rate-Limit-Remaining` and `X-Rate-Limit-Window`

### Webhook events
- `payment_success` — Payment received
- `payout_success` — Transfer completed
- `payment_failed` — Payment attempt failed
- `payout_failed` — Transfer failed
- `payment_reversal` — Payment reversed
- `payout_refund` — Transfer refunded

## Decision guidance

| Scenario | Use Checkout | Use Virtual Account | Use Direct Debit |
|----------|--------------|-------------------|------------------|
| One-time online payment | ✅ | ❌ | ❌ |
| Receive multiple transfers over time | ❌ | ✅ | ❌ |
| Recurring/subscription billing | ❌ | ❌ | ✅ |
| Customer-initiated payment | ✅ | ✅ | ❌ |
| Merchant-initiated debit | ❌ | ❌ | ✅ |

| Scenario | Use Static Virtual Account | Use Dynamic Virtual Account |
|----------|---------------------------|---------------------------|
| Permanent customer account | ✅ | ❌ |
| One-time or time-bound payment | ❌ | ✅ |
| Recurring payments | ✅ | ❌ |
| Invoice-specific payment | ❌ | ✅ |

| Scenario | Verify via webhook | Verify via API call |
|----------|-------------------|-------------------|
| Immediate confirmation needed | ❌ | ✅ |
| Asynchronous processing | ✅ | ✅ (poll) |
| Before delivering value | ✅ (verify signature first) | ✅ (server-side) |

## Workflow

### Accept online payments (checkout)
1. **Get credentials** — Retrieve `clientId`, `clientSecret`, `accountId` from dashboard
2. **Authenticate** — `POST /v1/auth/token/issue` with credentials; store `access_token` and `refresh_token`
3. **Configure webhook** — Set webhook URL and signature key in dashboard; subscribe to `payment_success`
4. **Create order** — `POST /v1/checkout/order` with amount, currency, customer email, callback URL
5. **Display link** — Redirect customer to `checkoutLink` from response
6. **Receive webhook** — Nomba sends `payment_success` event; verify signature using `nomba-signature` header
7. **Verify transaction** — `GET /v1/transactions/accounts/single` with `transactionRef` from webhook; check `status: "SUCCESS"`
8. **Deliver value** — Only after server-side verification

### Create a virtual account
1. **Authenticate** — Obtain `access_token` as above
2. **Create account** — `POST /v1/accounts/virtual` with `accountRef`, `accountName`, `currency`
3. **Optional fields** — Add `expiryDate` for dynamic accounts, `expectedAmount` for exact-amount-only accounts
4. **Store details** — Save returned `bankAccountNumber` and `bankAccountName`
5. **Share with customer** — Provide account number for transfers
6. **Monitor transfers** — Receive `payment_success` webhooks when funds arrive

### Process a bank transfer
1. **Authenticate** — Obtain `access_token`
2. **Fetch banks** — `GET /v1/transfers/bank` to get bank codes
3. **Lookup account** — `POST /v1/transfers/bank/lookup` with account number and bank code
4. **Verify name** — Confirm returned account name matches recipient
5. **Transfer** — `POST /v2/transfers/bank` with amount, account number, bank code, `merchantTxRef` (unique ID for idempotency)
6. **Check status** — Response includes `status` field; `SUCCESS` = immediate, `PENDING_BILLING` = wait for webhook
7. **Verify webhook** — Receive `payout_success` or `payout_failed` event
8. **Handle refunds** — If `status: "REFUND"`, funds were returned; safe to retry

### Set up webhooks
1. **Dashboard** — Go to Developer → Webhook Setup
2. **Add URL** — Enter publicly accessible webhook endpoint (use ngrok for local testing)
3. **Set signature key** — Create a secret key for HMAC verification
4. **Subscribe to events** — Select `payment_success`, `payout_success`, etc.
5. **Verify signature** — On receipt, compute HMAC-SHA256 of payload using signature key; compare with `nomba-signature` header
6. **Idempotency** — Store `requestId` to avoid processing duplicate webhooks
7. **Return 2XX** — Respond with HTTP 200–299 within timeout; Nomba retries with exponential backoff if not

### Test in sandbox
1. **Use sandbox credentials** — Generate token with test `clientId`/`clientSecret` at `https://sandbox.nomba.com`
2. **Create order** — `POST /sandbox/checkout/order` (note `/sandbox/` path)
3. **Use test cards** — `5434621074252808` (OTP required), `4000000000002503` (3DS), `5484497218317651` (declined)
4. **Submit OTP** — Use `9999` for approval, `1234` for timeout, `5464` for invalid
5. **Verify transaction** — `GET /sandbox/checkout/transaction` with `orderReference`
6. **Check webhook** — Sandbox fires webhooks synchronously; verify signature headers

## Common gotchas

- **Mixing environments** — Using sandbox credentials with `https://api.nomba.com` or vice versa causes `401` errors. Always pair credentials with matching base URL.
- **Ignoring the `code` field** — HTTP 200 does not mean success. Always check `response.code === "00"`.
- **Not verifying webhook signatures** — Malicious actors can send fake webhooks. Always verify `nomba-signature` header using your secret key before processing.
- **Relying on webhooks alone** — Webhooks can be delayed or lost. Always verify transactions server-side with `GET /v1/transactions/accounts/single` before delivering value.
- **Token expiry** — Access tokens expire after 30 minutes. Refresh proactively (5 minutes before expiry) using `refresh_token` to avoid mid-request failures.
- **Missing `accountId` header** — Most endpoints require `accountId` in headers. Omitting it causes `400` or `401` errors.
- **Transfer rate limits** — Only 5 transfers to the same recipient per minute. Space out repeat transfers or implement a queue.
- **Virtual account limits** — Each user can create max 2 virtual accounts; each accepts up to ₦150. Use `expectedAmount` carefully—once set, only that exact amount is accepted.
- **Sandbox data expiry** — Checkout orders expire after 48 hours in sandbox; virtual account data is not persisted to production.
- **Idempotency key format** — Use UUID v4 for `X-Idempotent-key` header on transfers; same key with identical request returns original response, different request returns error.
- **Webhook retry backoff** — Failed webhooks retry up to 5 times with exponential backoff (2 min, 5 min, 11 min, 24 min, 53 min). Ensure your endpoint is stable.
- **3DS and OTP flows** — Card payments may require 3DS authentication or OTP. Checkout handles this automatically; do not skip the hosted page.
- **Transfer status `PENDING_BILLING`** — Transfer is queued; wait for webhook or poll with transaction ID. Do not assume failure if not immediate.

## Verification checklist

Before submitting work:
- [ ] Credentials are paired with correct base URL (sandbox with sandbox, production with production)
- [ ] All API responses check `code === "00"` before processing `data`
- [ ] Webhook signature is verified using HMAC-SHA256 before processing payload
- [ ] Transaction is verified server-side with `GET /v1/transactions/accounts/single` before delivering value
- [ ] `access_token` is refreshed before expiry (check `expiresAt` field)
- [ ] `accountId` header is included in all authenticated requests
- [ ] Unique `merchantTxRef` is generated for each transfer (idempotency)
- [ ] Webhook endpoint returns HTTP 2XX within timeout
- [ ] Rate limits are respected (15 POST/sec for regular accounts, 5 transfers/min to same recipient)
- [ ] Sandbox testing is complete before moving to production
- [ ] Error responses are logged with `code` and `description` for debugging

## Resources

- **Comprehensive page listing:** https://developer.nomba.com/llms.txt
- **API Reference (interactive):** https://developer.nomba.com/nomba-api-reference/introduction
- **Sandbox Testing Guide:** https://developer.nomba.com/docs/products/accept-payment/sandbox-testing
- **Webhook Setup & Verification:** https://developer.nomba.com/docs/api-basics/webhook
- **Authentication:** https://developer.nomba.com/docs/getting-started/authentication

---

> For additional documentation and navigation, see: https://developer.nomba.com/llms.txt