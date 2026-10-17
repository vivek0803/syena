# Syena (श्येन)

A single-user, transparent auto-trading and portfolio platform for Indian equities (NSE).
Patience for long-term investing, speed for news trading, precision for algo trading.

Every decision the system makes, including the decisions *not* to trade, is recorded with the exact
inputs, reasoning, checks passed and failed, lessons consulted, and resulting order. Default mode is **PAPER**.

## What's inside

| Area | What it does |
|---|---|
| **Algo builder** | Plain-English rules ("price closes below lower Bollinger band and RSI < 30") become a validated JSON rule. A built-in grammar handles common phrasing instantly; anything else goes to the AI parser, whose output is schema-validated. Ambiguous or unsupported rules get a clarifying question, never a guess. Backtest with next-bar fills, slippage, full Zerodha charges and the ₹ position-size band, then run in paper (live after the gate). |
| **News trading** | RSS/NSE feeds → dedupe → stock mapping → triage (Haiku) → analysis (Sonnet) → trade gate (confidence, technicals, market context from Nifty/Sensex/India VIX, sizing, risk limits, lessons, optional second opinion in LIVE). |
| **Manual invest** | You propose a trade; AI reviews it against news, technicals and lessons; nothing is ordered until you click Yes. |
| **Portfolio X-Ray** | Zerodha (equity + Coin MFs), Angel One (read-only) and mutual funds from a CAMS/KFintech/MF Central CAS PDF in one view: % per holding, per account, per asset class; **look-through exposure** (direct + via each fund); **which of your funds hold each stock and at what weight**; fund-vs-fund overlap; sector exposure; concentration warnings. Fund holdings come from each AMC's monthly portfolio Excel (upload or URL). |
| **Position monitor** | Always-on worker: live prices via KiteTicker, stop/target, intraday square-off before 15:15, swing gap-downs, max holding period, negative news. Auto-sells where enabled; otherwise sends a Telegram SELL alert with Approve / Reject (LIVE sells need a second tap). |
| **Evals** | Parser golden set (73 cases), news-signal replay with strict no-lookahead, calibration, confusion matrix and baselines; triage/mapping; LLM-as-judge (different model); model comparison; online scoring at 1h/1d/5d; drift alerts; shadow (challenger) model. |
| **Learning loop** | Every closed trade is reviewed (Opus tier), separating decision quality from luck, with computed counterfactuals. Rejected signals are scored as missed opportunities or correctly avoided. Lessons go PROPOSED → TESTING (shadow) → ACTIVE only after N independent trades across distinct days, an out-of-sample check that excludes a permanent holdout, **and your approval**. Weekly statistical pattern mining with confidence intervals. |
| **Safety** | Single user, argon2id + mandatory TOTP, lockout, CSRF + Origin checks, default-deny routes, encrypted secrets at rest, kill switch in UI and Telegram (`/halt`), automatic fallback to PAPER on restart, token expiry, daily-loss breach and repeated broker errors. **LIVE can only be enabled in the web UI** with password + TOTP + typing `LIVE`, plus a per-strategy LIVE gate (backtest/replay thresholds, minimum paper trades and days, paper within tolerance of backtest). |

## Repository layout

```
backend/   FastAPI app, worker, SQLAlchemy models, Alembic migrations, 124 tests
frontend/  React + Vite + TypeScript + Tailwind + TanStack Query + Recharts
evals/     golden datasets: parser/*.yaml (73 cases), triage/*.yaml
deploy/    Caddyfile, docker-compose.prod.yml, deploy.sh, restore.sh, .env.example
docs/      the original project spec
```

```
            browser (HTTPS)                   Telegram
                  │                               │ webhook (secret + chat id)
             ┌────▼─────┐                         │
             │  Caddy   │ static UI, TLS, HSTS    │
             └────┬─────┘                         │
                  │ /api, /kite, /health          │
             ┌────▼─────┐   ┌───────────┐         │
             │   API    │◄──┤  Postgres │◄──┐     │
             │ FastAPI  │   └───────────┘   │     │
             └──────────┘                   │     │
             ┌──────────────────────────────┴─────▼──┐
             │ Worker: KiteTicker, algo runner, news, │──► Zerodha Kite Connect
             │ monitor, reviews, evals, summaries     │──► Anthropic API
             └────────────────────────────────────────┘
```

## Run it locally

```bash
# backend
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp ../.env.example .env && echo "SYENA_ENCRYPTION_KEY=$(syena gen-key)" >> .env
alembic upgrade head
syena create-admin            # username, password (12+ chars), scan the TOTP QR in your authenticator
syena seed-demo               # optional: synthetic PAPER data so every page has something to show
uvicorn syena.main:app --reload            # API on :8000 (docs at /api/docs in dev)
python -m syena.worker.main                # background worker (separate terminal)

# frontend (another terminal)
cd frontend && npm install && npm run dev  # http://localhost:5173, proxies /api to :8000

# tests and the eval gate
cd backend && pytest && syena eval-gate
SYENA_TEST_DATABASE_URL=postgresql+psycopg://user@host/db pytest   # same suite on Postgres
```

## Deploy to your domain

No domain or paid server? A free Oracle Cloud Always Free server (Mumbai/Hyderabad) plus a free DuckDNS name works
exactly the same way: set `SYENA_DOMAIN` to e.g. `vivek-syena.duckdns.org`. The steps below apply unchanged.


**1. Server.** Any Linux VPS in an India region with a **static public IP** (e.g. AWS Mumbai with an Elastic IP,
DigitalOcean Bangalore with a reserved IP). Install Docker and the Compose plugin. Open only ports 22, 80 and 443.

**2. DNS.** Point an A record for your domain (e.g. `syena.yourdomain.com`) at the server's static IP.

**3. Zerodha developer console** (developers.kite.trade):
- Redirect URL: `https://syena.yourdomain.com/kite/callback`
- **Register the server's static IP.** Since April 2026 Zerodha rejects API orders from unregistered IPs.
  Syena stays well below the 10 orders/second threshold that would require exchange algo registration.

**4. Install.**
```bash
git clone https://github.com/vivek0803/syena.git ~/syena && cd ~/syena
cp deploy/.env.example deploy/.env && nano deploy/.env      # domain, passwords, API keys
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env run --rm api syena gen-key   # paste into SYENA_ENCRYPTION_KEY
./deploy/deploy.sh            # builds, runs tests + eval gate, migrates, starts everything
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env run --rm api syena create-admin
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env run --rm api \
  syena telegram-webhook https://syena.yourdomain.com/api/telegram/webhook
```

**5. First login.** Open `https://syena.yourdomain.com`, sign in with your password and authenticator code,
click **Connect Zerodha**, then in Settings → Security send a Telegram test message.

**Redeploys:** `./deploy/deploy.sh` (or push to `main` with the optional deploy secrets in GitHub Actions).
Every redeploy comes back in PAPER. **Backups:** an encrypted `pg_dump` runs daily into `deploy/backups/`
(14 days kept); copy them off the server. Restore with `./deploy/restore.sh <file>`.

## Your daily routine

1. Morning: open Syena, tap **Connect Zerodha** (Kite tokens expire around 06:00 IST; Telegram reminds you).
2. The worker runs algos on each completed candle, polls news every 5 minutes and checks positions every minute.
3. Approve or reject SELL alerts on Telegram. `/halt` stops everything instantly; `/status` shows P&L.
4. 15:45: daily summary on Telegram. Saturdays: weekly learning digest with lessons awaiting your approval.

## Portfolio X-Ray: getting the data in

- **Zerodha:** Portfolio → Sync (needs today's Zerodha connection). Includes Coin mutual funds.
- **Angel One:** Portfolio → Log in and sync with client code, PIN and Angel's authenticator code. Credentials are
  used once and never stored; holdings only, no orders.
- **Mutual funds:** request a *detailed* CAS from camsonline.com or mfcentral.com (covers CAMS + KFintech),
  then upload the PDF with its password. An NSDL/CDSL depository CAS also works and adds stocks held at other brokers.
- **What's inside each fund:** download each AMC's monthly portfolio Excel and upload it (or paste the link).
  Schemes are matched to your funds by name; the stock-level look-through and overlap then fill in.

## Defaults (all editable in Settings)

| Setting | Default |
|---|---|
| Position size | ₹7,000 to ₹10,000 (qty = floor(₹10,000 / price)) |
| Risk limits | 5 open positions, 10 trades/day, ₹3,000 daily loss halt, ₹10,000 per stock |
| Trade gate | confidence ≥ 70, technicals must not be bearish, market context ≥ −0.3 |
| Intraday | no entries after 14:45, square-off 15:10 |
| Swing | max 20 trading days, exit alert on a 4% gap down |
| Models | triage `claude-haiku-5-5`, analysis `claude-sonnet-5-5`, review `claude-opus-5-5`, judge `claude-sonnet-5-5`; second opinion and shadow model unset |
| AI budget | $5/day (evals excluded) |
| Market context | 50% Nifty, 20% Sensex, 30% inverse India VIX |
| Lessons | TESTING needs 5 supporting trades over ≥ 3 days; max 25 active; re-validated every 30 days |
| LIVE gate | ≥ 20 backtest trades, win rate ≥ 45%, Sharpe ≥ 0.5, drawdown ≤ 20%, ≥ 15 paper trades over ≥ 10 days, paper win rate within 15 points of backtest |
| Exits | auto-sell on for algo and news trades, off for manual invests (toggle per position) |
| News | NSE announcements + 3 market RSS feeds, 10-stock watchlist |

## Verified here, and what still needs your real accounts

Verified in development:
- 124 backend tests (deterministic gate/sizing/risk/charges/PaperBroker, auth, mode-switch property tests, lesson promotion
  rules, parser, backtester, news pipeline, replay no-lookahead, portfolio look-through, monitor, Telegram flows, API).
- The whole suite and the migrations also pass on **PostgreSQL 16**; the worker runs cleanly against it.
- Parser golden set: 73/73 on the built-in grammar. The set was written alongside the grammar, so add every phrasing
  that trips it up to `evals/parser/` — that is what keeps the number honest.
- The frontend type-checks and builds; every page was reviewed with seeded data at desktop and phone widths.

Not exercised against the real services from this environment (all are mocked in tests). Check these on paper first:
- Zerodha Kite (login, historical data, quotes, orders, KiteTicker reconnects) and Angel One SmartAPI endpoints.
- Telegram delivery and buttons, the Anthropic API, and the live news feeds (feed URLs change; edit them in Settings).
- The Docker images and Caddy were written but not built here (no Docker daemon); `deploy.sh` builds and tests them first.
- AMC portfolio files vary by fund house. The parser finds ISIN / name / "% to NAV" columns heuristically; tell me
  if one of your AMCs doesn't parse and I'll add an adapter.
- Zerodha charge rates (2026 schedule) are in `backend/syena/market/charges.py`; verify against zerodha.com/charges.
- There is no fundamentals feed: the P/E in Manual invest is what you type, and the review treats it as unverified.
- Lesson retrieval uses local hashed embeddings instead of pgvector (lessons are capped at 25 active, so it's fast);
  swapping in pgvector later doesn't change callers.
