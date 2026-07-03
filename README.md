# TravixPay

A fintech-grade transportation payment platform for Nigerian public transit.

## Overview

TravixPay lets passengers fund wallets via Nomba hosted checkout, link RFID cards, and pay bus fares by tapping cards on Arduino terminals. Drivers verify payments in real-time. Admins monitor system health.

### Key Workflows

- **Passenger:** Register → Fund wallet (Nomba checkout) → Link RFID card → Tap on bus → Fare deducted
- **Driver:** View terminal dashboard → See passenger taps → Track daily collections
- **Admin:** Monitor payments → View audit logs → Check system health

---

## Development Setup

```bash
cd Another

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies (no Redis needed)
pip install django requests

# Copy environment file
cp .env.example .env

# Run migrations
python manage.py migrate

# Create superuser (admin)
python manage.py createsuperuser

# Run server
python manage.py runserver
```

Visit `http://127.0.0.1:8000/auth/register/` to create an account.

---

## Environment Variables

| Variable | Sandbox | Production | Default | Description |
|---|---|---|---|---|
| `DJANGO_SECRET_KEY` | Yes | Yes | (dev default) | Django secret key |
| `DEBUG` | No | No | `True` | Debug mode |
| `NOMBA_ENV` | No | Yes | `sandbox` | `sandbox` or `production` |
| `PAYMENT_CONFIRMATION_MODE` | No | Yes | `VERIFY_ONLY` | `VERIFY_ONLY` or `WEBHOOK_REQUIRED` |
| `NOMBA_CLIENT_ID` | **No** | Yes | — | Nomba API client ID |
| `NOMBA_CLIENT_SECRET` | **No** | Yes | — | Nomba API client secret |
| `NOMBA_PARENT_ACCOUNT_ID` | **No** | Yes | — | Nomba parent account ID |
| `NOMBA_WEBHOOK_SECRET` | **No** | Yes | — | Nomba webhook HMAC secret |

**Sandbox requires zero Nomba credentials.** The gateway calls `https://sandbox.nomba.com` directly without authentication.

---

## Sandbox Setup (Hackathon)

No Redis. No Celery. No credentials. No public URL. No ngrok.

```bash
# .env — minimal config, everything else uses defaults
NOMBA_ENV=sandbox
```

That's it. No Nomba credentials needed.

### How it works

Nomba sandbox accepts requests without authentication. The gateway:
- Skips token generation
- Skips Bearer Authorization header
- Skips accountId header
- Calls `https://sandbox.nomba.com/v1/checkout/order` directly

### Funding flow

1. User clicks "Fund Wallet" → enters amount
2. System creates PaymentIntent → calls Nomba sandbox → gets checkout link
3. User redirected to Nomba hosted checkout page
4. User pays (sandbox simulates success)
5. Nomba redirects to `/payments/callback/?reference=TX-ABC123`
6. System calls Nomba verification API → confirms payment
7. Wallet credited instantly → user sees success page

No webhook needed. No queue needed. Synchronous verification.

---

## Production Setup

```bash
# .env
NOMBA_ENV=production
PAYMENT_CONFIRMATION_MODE=WEBHOOK_REQUIRED
NOMBA_CLIENT_ID=your-client-id
NOMBA_CLIENT_SECRET=your-client-secret
NOMBA_PARENT_ACCOUNT_ID=your-parent-account-id
NOMBA_WEBHOOK_SECRET=your-webhook-secret
```

### How it works

Production mode (`NOMBA_ENV=production`):
- Obtains Bearer token via `POST /v1/auth/token/issue`
- Caches token with expiry buffer
- Attaches `Authorization` and `accountId` headers to every request
- Rate limiting and circuit breaker enforced

Webhook mode (`PAYMENT_CONFIRMATION_MODE=WEBHOOK_REQUIRED`):
1. User pays on Nomba checkout
2. Nomba sends webhook to `/payments/webhook/`
3. System verifies HMAC signature → verifies payment → credits wallet
4. Callback page shows "payment received, crediting shortly"

### Webhook registration

Register your webhook URL in the Nomba dashboard:
```
https://yourdomain.com/payments/webhook/
```

---

## Project Structure

```
travixpay/          Django project settings
accounts/           User auth, registration, profiles
wallets/            Wallet model, ledger, virtual accounts
payments/           Payment intents, checkout, orchestrator, gateway
cards/              RFID card management
terminals/          Arduino tap processing, fare rules
events/             Event dispatcher, domain events, DLQ
audit/              Audit log model
drivers/            Driver profiles, serial bridge
admin_panel/        Admin dashboard, audit log views
frontend/           Templates, static files, design system
```

---

## Architecture

```
User/HTMX → Django Views → PaymentOrchestrator → NombaGateway → Nomba API
                                    ↓
                            WalletLedger (CREDIT/DEBIT)
                                    ↓
                              AuditLog

Arduino → Serial Bridge → TapProcessor → WalletLedger (DEBIT)
```

- **Single gateway:** All Nomba calls go through `payments/services/nomba_gateway.py`
- **Ledger-based:** Wallet balance = SUM(credits) - SUM(debits). No balance field.
- **Idempotent:** Unique references prevent duplicate credits/taps
- **Event-driven:** Important actions emit DomainEvent records

---

## Troubleshooting

### "Nomba auth failures"
- Check `NOMBA_CLIENT_ID` and `NOMBA_CLIENT_SECRET` in `.env`
- Ensure `NOMBA_ENV` matches your credentials (sandbox vs production)

### "Client suspended" or "Risk Control 441"
- Too many requests. System has circuit breaker protection.
- Wait 60 seconds, circuit breaker auto-recovers.
- Check `payment.log` for details.

### "Payment not verified"
- In VERIFY_ONLY mode, callback calls Nomba API to confirm.
- If Nomba is down, payment stays pending. User can retry.
- Check if `NOMBA_ENV` matches the checkout environment.

### "Duplicate payment"
- System uses unique references (`TX-XXXXXXXXXXXX`) per PaymentIntent.
- `WalletLedger` entries are idempotent via unique `reference` constraint.
- Webhook events are deduplicated via `event_id` unique constraint.

### "Webhook not received"
- Only works in `WEBHOOK_REQUIRED` mode.
- In `VERIFY_ONLY` mode, webhook endpoint returns `{"status": "disabled"}`.
- Check webhook URL registration in Nomba dashboard.

---

## Arduino / Serial Bridge

```bash
# Run the serial bridge (connects Arduino to backend)
python drivers/serial_bridge.py --port /dev/ttyUSB0 --backend http://localhost:8000 --terminal TERM-001 --fare 200
```

The bridge handles:
- Online mode: sends tap to backend API
- Offline mode: stores taps locally (max 2 rides, ₦1000 debt)
- Reconciliation: syncs offline taps when backend is reachable

---

## License

MIT
