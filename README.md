# Syena (श्येन)

Single-user, transparent auto-trading and portfolio platform for Indian equities (NSE).
Patience for long-term investing, speed for news trading, precision for algo trading.

Every decision the system makes is explainable in the UI. Default mode is **PAPER**.

## Repository layout

| Path        | What                                                        |
|-------------|-------------------------------------------------------------|
| `backend/`  | FastAPI app, SQLAlchemy models, Alembic migrations, tests   |
| `frontend/` | React + Vite dashboard (Phase 7)                             |
| `evals/`    | Golden datasets and eval suites (Phase 4 onward)             |
| `deploy/`   | Caddy, docker-compose, backup and deploy scripts (Phase 8)   |

## Build status

| Phase | Scope | Status |
|---|---|---|
| 1 | DB, backend skeleton, logging, auth (single user, TOTP), mode control | Done |
| 2 | Kite data layer, PaperBroker, sizing, risk limits, deterministic tests | Next |
| 3 | Portfolio X-Ray: Zerodha + Angel One + CAMS CAS, look-through, MF overlap | |
| 4 | Algo Builder, backtester, parser evals | |
| 5 | News/AI pipeline, replay engine, evals, post-trade reviewer, lessons (PROPOSED) | |
| 6 | Position monitor, Telegram, online evals, lesson retrieval and shadow mode | |
| 7 | Frontend: all pages, LIVE gates, lesson approval | |
| 8 | Deployment: domain, HTTPS, VPS, backups, CI gate | |

## Local development

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp ../.env.example .env
syena gen-key                       # paste into SYENA_ENCRYPTION_KEY in .env
alembic upgrade head
syena create-admin                  # username, password (12+ chars), scan the TOTP QR
uvicorn syena.main:app --reload     # API docs at http://localhost:8000/api/docs (dev only)
pytest
```

## Phase 1: what exists

**Authentication** (`syena/services/auth.py`, `syena/api/auth.py`)
- One user, created only by `syena create-admin`. No signup and no email reset.
- argon2id hashing (64 MiB, t=3, p=4) and a 12-character minimum password.
- Mandatory TOTP on every login, with replay protection: each 30-second code is accepted once.
- Lockout after 5 failures for 15 minutes, plus a per-IP limit of 10 login attempts per minute.
  Every failure returns the same "Invalid credentials" message.
- Server-side sessions. The cookie is HttpOnly, Secure and SameSite=Strict, and the DB stores only its SHA-256.
  Sessions expire after 30 minutes idle or 12 hours absolute.
- CSRF token (`X-CSRF-Token` header) plus an Origin check on every state-changing request.
- Default-deny: every route except `/health` and `/api/auth/login` returns 401 without a session.
  A test enumerates all routes to keep it that way.
- Security headers: CSP, `frame-ancestors 'none'`, X-Frame-Options, nosniff, HSTS, no-referrer.
  CORS is locked to `SYENA_PUBLIC_ORIGIN`. API docs are disabled outside dev.
- Every login attempt is audited (success or failure, IP, user agent). See `GET /api/auth/logins`.

**Mode control** (`syena/services/mode.py`, `syena/api/mode.py`)
- Mode lives only in `system_state`. There is no env var, config key or CLI command for it.
- Going LIVE needs an authenticated session, password re-entry, a fresh TOTP code, and the exact text `LIVE`.
  It is also refused while halted or while any registered precondition fails.
  Phase 2 registers the Kite-token check.
- Every process start forces PAPER. Switching back to PAPER is one click.
- Kill switch: `POST /api/mode/halt` drops to PAPER and blocks LIVE until resumed.
- Every change and every denied LIVE attempt goes to `mode_audit_log` with the IP.

**Logging** (`syena/logging_setup.py`, `GET /api/logs`)
- JSON logs to stdout, plus `system_logs` in the DB, filterable by level, module, stock, algo and text.
- Keys that look sensitive (password, token, secret, totp, api_key, ...) are redacted recursively,
  and the encryption key's value is scrubbed from messages.

**Tests**: 31 tests, including Hypothesis property tests. They prove there is no LIVE switch without
all three factors and no code path to mode outside the mode service. They also check that migrations match the models.

## API (Phase 1)

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | public |
| POST | `/api/auth/login` | public; `{username, password, totp}` and returns `csrf_token` |
| GET | `/api/auth/me` | returns the current CSRF token |
| POST | `/api/auth/logout` | |
| GET | `/api/auth/logins` | login history |
| GET | `/api/mode` | mode, halted, reason |
| POST | `/api/mode/live` | `{password, totp, confirmation: "LIVE"}` |
| POST | `/api/mode/paper` | one click |
| POST | `/api/mode/halt` / `/api/mode/resume` | kill switch |
| GET | `/api/mode/audit` | mode change history |
| GET | `/api/logs` | `level, module, stock, algo_id, q, before_id, limit` |

## Defaults chosen (change any in review)

- AI tiers: triage `claude-haiku-5-5`, analysis `claude-sonnet-5-5`, review `claude-opus-5-5`.
  The second-opinion model is pluggable and unset.
- Risk limits: max 5 open positions, max daily loss ₹3,000 (auto-halt), max ₹10,000 exposure per stock,
  and max 10 trades per day. Confidence threshold 70.
- Market context score: 50% Nifty intraday change, 20% Sensex, 30% inverse India VIX change.
- News v1: NSE and BSE corporate announcements plus a handful of financial RSS feeds.
- Angel One is read-only (holdings only, no orders).
- Telegram SELL approvals rely on the verified chat ID. LIVE sells also require tapping a second confirm.
- SEBI retail-algo rules: Syena stays under the order-rate threshold that triggers algo registration,
  and orders come from the VPS's whitelisted static IP. This is verified against Zerodha's current
  requirements before the LIVE path ships.
