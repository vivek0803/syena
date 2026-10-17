# AlgoDesk: Project Spec (prompt for Claude Code)

You are a senior full-stack engineer. Build "AlgoDesk": a single-user, auto-trading platform for Indian equities (NSE), deployed on my own domain. Frontend: React + Vite. Radical transparency is the core requirement: every decision the system makes must be explainable in the UI. This REPLACES my existing news/Kafka pipeline. Do not depend on it.

Before writing code: read this whole spec, list any ambiguities or questions, then present a phased plan. Build one phase at a time and wait for my approval before moving on.

---

## 1. Stack
- Frontend: React + Vite + TypeScript, Tailwind, TanStack Query, Recharts, live updates via WebSocket/SSE. Dark-mode dashboard, mobile-friendly.
- Backend: Python 3.11, FastAPI, APScheduler, SQLAlchemy + Alembic.
- DB: PostgreSQL (SQLite for local dev). Add a vector index (pgvector or equivalent) for lessons retrieval.
- Broker/data: Zerodha Kite Connect (paid Connect plan).
- Alerts: Telegram bot.
- AI: pluggable LLM client with model tiers set in config:
  - triage = claude-haiku-4-5-20251001
  - analysis/parsing = claude-sonnet-5-5
  - high-stakes review = claude-opus-5-5
  - Optional second-opinion model (consensus required in LIVE mode only).
  - Log token usage and cost per call.

## 2. Modes
- BACKTEST: historical simulation.
- PAPER: everything runs exactly like live (data, signals, AI analysis, monitoring, exits, Telegram alerts) EXCEPT the real order call. Fills are simulated from live quotes with slippage and brokerage/STT/charges, against a dummy wallet (configurable, default 1,00,000). Paper positions are tracked, exited, and show realised/unrealised P&L.
- LIVE: real Kite orders.

Use one execution interface with two implementations (PaperBroker, KiteBroker) so all other code is identical across modes.

## 3. Data layer (Kite Connect)
- KiteTicker WebSocket for live prices (paper fills, stop/target checks, indicator updates). REST historical candles for backtests and warm-up.
- Throttle historical requests (about 3/sec) and cache candles in the DB.
- Handle daily Kite login/token expiry and WebSocket reconnects.
- Market orders via API need market price protection set, or they are rejected. Prefer limit orders or set protection explicitly.
- Market data is for my own use only. Never redistribute it.

## 4. Position sizing (hard constraint)
- Every algo trade and new investment uses 7,000 to 10,000 per position. qty = floor(amount / price).
- If one share costs more than 10,000, or no valid qty gives 7,000 to 10,000, reject with a logged reason. Min/max configurable.

## 5. Trading styles
- INTRADAY (MIS) and SWING (CNC), tagged per algo/trade.
- Intraday: no new entries after a cutoff (default 14:45), auto square-off before 15:15 IST, tighter stops.
- Swing: overnight holds, gap handling, max holding period.

## 6. Module 1: Algo Builder (free English)
- UI: text box for plain-English logic, mode dropdown (Backtest / Paper / Live), style dropdown (Intraday / Swing), Build button.
- Must work: "buy when SMA 20 crosses above SMA 50", "ema(50) > ema(200)", "price closes below lower Bollinger band and RSI < 30", "MACD bullish crossover with volume above 20-day average".
- An LLM parses the text into a validated JSON-schema rule over a supported indicator library: SMA, EMA, WMA, RSI, MACD, Bollinger Bands, ATR, VWAP, Stochastic, ADX, Supertrend, volume, OHLC, crossovers, and and/or/not combinations (pandas-ta or TA-Lib).
- Show the parsed rule in readable form for me to confirm or edit. If ambiguous or unsupported, ask or say so. Never guess silently.
- On confirm: fetch history, backtest, show metrics (win rate, drawdown, Sharpe, equity curve, trade list), then start in the chosen mode.
- Stop-loss/target come from the text or defaults (e.g. ATR-based).

## 7. Module 2: AI News-Driven Auto Trading
- Pipeline: news sources (RSS, exchange filings, OSINT feeds) -> dedupe -> entity/stock mapping -> AI analysis -> signal.
- Signal: buy/sell/hold, confidence 0-100, reasoning, cited headlines, technical indicators, and a market context score from Nifty/Sensex movement and India VIX (configurable).
- Trade gate: order only if confidence >= threshold AND technicals and market context don't contradict AND sizing rule passes AND risk limits pass AND no ACTIVE lesson blocks it (section 18). Otherwise log a REJECTED decision with the exact failing check.

## 8. Module 3: Assisted Manual Invest
- Form: stock symbol, entry price range or market entry, entry reason, current P/E, exit condition, target, stop-loss, holding period, style.
- On submit: AI reviews fundamentals, news, technicals and relevant lessons; returns verdict, flagged issues, suggested corrections.
- I Confirm or Edit. On "Yes" the order is placed (paper or live per mode) and written to the DB. Nothing is ordered before confirmation.

## 9. Position Monitor
- In market hours (9:15-15:30 IST, holiday-aware), scan every 30 min: exit conditions, stop-loss, target, fresh news on held stocks.
- If a condition is met or news is materially negative, send a Telegram SELL alert with the reason and Approve/Reject buttons. Approve -> sell (paper or live). Reject -> log and snooze. Optional per-position auto-sell toggle. Works identically in paper mode.
- Runs in a separate always-on worker, independent of any browser.

## 10. Trades table (minimum)
algo_id, mode, style, algo_condition, stock, qty, invested_amount, entry_price, exit_price_defined, exit_price_executed, stop_loss, target, charges, profit_loss, exit_reason, status, created_at, closed_at.

## 11. Frontend visibility (must-have)
1. Dashboard: paper vs live P&L, wallet balance, open positions, today's decisions, system health, Kite token status, always-visible mode badge.
2. Decision Log: timeline of EVERY decision (placed, rejected, skipped, held, exited). Click for full explanation: inputs, parsed rule or AI reasoning, confidence, checks passed/failed, lessons consulted/applied, resulting order.
3. News Feed: every article analysed, with source, time, mapped stocks, sentiment, AI summary, and the trades it influenced.
4. Algo Inspector: English text, parsed rule, live indicator values vs thresholds, signals fired, backtest results.
5. Trades & Positions: lifecycle table with realised/unrealised P&L.
6. Logs: structured, searchable, filterable (level, module, stock, algo).
7. Orders: status and broker rejection messages.
8. Evals: see section 17.
9. Learning: see section 18.
10. Settings: sizing, risk limits, confidence threshold, paper wallet, model tiers, AI usage/cost, login history, mode control, eval/lesson thresholds.

All data via API endpoints; the UI never touches the DB.

## 12. Data model (minimum)
users, sessions, algos, trades, decisions (type, reason_json, checks_json, confidence, trade_id, lessons_consulted), news_items, news_analyses, signals, positions, paper_wallet, candles_cache, order_events, system_logs, alerts, mode_audit_log, login_audit_log, ai_calls, eval_datasets, eval_runs, eval_results, trade_reviews, lessons, lesson_evidence, lesson_applications.
Store prompt, prompt version, raw response, model, latency, and cost for every AI call.

## 13. Safety (non-negotiable)
- Default mode is Paper.
- Risk limits: max open positions, max daily loss (auto-halt), per-stock exposure cap, max trades per day.
- Global kill switch in UI and Telegram (/halt).
- Idempotent orders, retries with backoff, circuit-limit and market-hours checks. Secrets in env only, never logged.

## 14. Deployment & access (single-user, internet-facing)
This app can place real orders, so treat it as high-security.

### Hosting
- docker-compose stack on one VPS (India region, fixed public IP): Caddy (reverse proxy, automatic HTTPS) -> FastAPI backend + built React static files + PostgreSQL + scheduler worker. Only 80/443 (and SSH) are public. Postgres and backend are reachable only inside the Docker network.
- Domain: https://<mydomain>. HTTP redirects to HTTPS, HSTS enabled.
- Provide: Caddyfile, docker-compose.prod.yml, .env.example, deploy script (git pull, build, migrate, restart), daily encrypted DB backups, healthcheck endpoint, restart-on-failure.

### Authentication (one user only)
- No signup page and no email password reset. One account created via a CLI command (create-admin) with a username and password I choose.
- argon2id password hashing. Login rate limiting, lockout after repeated failures, generic error messages.
- HttpOnly + Secure + SameSite=Strict session cookie, idle timeout, CSRF protection on all state-changing requests.
- Mandatory TOTP 2FA (authenticator app).
- ALL API routes and the WebSocket/SSE stream require authentication, except /login and /health. Telegram webhook verified with a secret token and my chat ID.
- Security headers (CSP, X-Frame-Options, etc.), CORS locked to my domain.
- Log all logins (success/failure, IP, time) and show them in Settings.

### Paper/Live switch (frontend only)
- Mode lives in the DB and can be changed ONLY through the authenticated Mode control in the frontend. No env var, config file, CLI flag, or Telegram command can switch it to LIVE.
- Switching to LIVE requires password re-entry + TOTP code + typing "LIVE" in a confirmation dialog showing the risk limits.
- Auto-fallback to PAPER on: restart/redeploy, Kite token expiry, max-daily-loss halt, repeated broker errors.
- Switching back to PAPER is one click. Open live positions stay monitored. No new live orders are placed in PAPER.
- Every mode change is written to an audit log (when, IP).
- Mode badge (PAPER green / LIVE red) visible on every page.

### Kite Connect on a domain
- Kite app redirect URL: https://<mydomain>/kite/callback.
- Daily login is a "Connect Zerodha" button in the UI. The backend exchanges the request token and stores the access token encrypted.
- Show token status/expiry on the dashboard and alert me on Telegram when it expires.
- API keys/secrets live only in server env or encrypted storage, never in the frontend or logs.

## 15. Enhancements
- Backtest-before-live gate and a minimum paper period before any algo can go live.
- Daily Telegram summary of P&L, trades, rejections.
- Paper vs live performance comparison per algo.
- CSV export of trades and decisions.

---

## 17. Evaluation framework (evals)
Goal: measure whether each AI component is correct, calibrated, and actually profitable, and block changes or going LIVE when it isn't.

### A. Rule-parser evals (English -> JSON)
- Golden dataset (start with 60+ cases in /evals/parser/*.yaml): plain cases ("ema(50) > ema(200)"), Bollinger/RSI/MACD combos, crossovers, and/or/not, typos, and ambiguous or unsupported inputs that MUST trigger a clarification instead of a guess.
- Metrics: exact-schema match, semantic equivalence (run both rules on the same candles and compare signals), clarification-correctness.
- Deterministic, no LLM judge needed.

### B. News-signal evals (replay)
- Build a labeled dataset from stored news + candles: for each item store the timestamp, the stock, and what price did over 1h / 1d / 5d after.
- Replay engine feeds the pipeline ONLY data available at that timestamp (strict no-lookahead; test for leakage explicitly).
- Metrics: direction accuracy, precision/recall of buy and sell calls, confidence calibration (does 80% confidence win about 80% of the time?), average return per signal after charges, hit rate vs baselines (random, buy-and-hold, Nifty).
- Report confidence intervals and sample size. Mark results "insufficient data" under a minimum sample, never as proven.

### C. Triage and mapping evals
- Labeled set for relevant/irrelevant news and article -> stock mapping.
- Track false negatives (missed material news) separately from false positives (noise cost).

### D. Reasoning-quality evals (LLM-as-judge, secondary)
- A separate judge prompt scores decision explanations for groundedness (every claim traceable to cited news/indicators), consistency with the action taken, and absence of hallucinated facts.
- Judge uses a different model than the one being evaluated. Spot-check judge scores against my manual labels.

### E. Deterministic tests (unit/integration, run every commit)
- Trade gate, sizing rule (7,000-10,000), risk limits, intraday cutoff and square-off, PaperBroker fills/charges/P&L, mode-switch rules, auth.
- Property tests: no order is ever placed in PAPER via KiteBroker; no LIVE switch without password + TOTP + confirmation.

### F. Model and prompt comparison
- Eval runner executes any dataset against multiple model/prompt configs (e.g. Haiku vs Sonnet for analysis) and outputs accuracy, calibration, cost, and latency side by side. Prompts are versioned; every decision stores its prompt version and model.

### G. Online evals (running system)
- Every signal and decision is auto-scored when its outcome is known (1h/1d/5d). Compare predicted vs actual, per algo and per model.
- Alert on drift: accuracy or calibration dropping below thresholds, parser clarification rate changing, cost per decision spiking.
- Shadow mode: new prompts/models run in parallel, logged but not traded, and are compared to the current champion before being promoted.

### H. Gates (enforced in code)
- CI fails if parser or deterministic evals drop below thresholds.
- A news-driven strategy or algo can't be switched to LIVE until it has (a) passed backtest/replay thresholds, (b) a minimum number of paper trades over a minimum number of days, and (c) paper results within an agreed range of replay results. Thresholds live in Settings.

### I. Frontend: "Evals" page
- Latest run per eval suite (pass/fail, trend over time), calibration chart, confusion matrix for buy/sell/hold, model comparison table, drift alerts, and a list of failed cases with the model's output and the expected answer. A "Run evals" button for on-demand runs.

---

## 18. Self-learning loop (post-trade review + lessons memory)

### A. Post-trade review (automatic, on every closed trade, paper or live)
- Trigger: when a trade closes (target, stop, signal exit, news exit, square-off), queue a review job.
- Reviewer inputs: the full decision record (entry reasoning, cited news, indicators, market context, confidence, parsed rule), the price path from entry to exit and 1d/5d afterwards, the exit reason, charges, and P&L. Use only information that existed at each point in time.
- The reviewer (high-stakes tier model, different prompt from the trader) outputs structured JSON:
  - outcome classification: good-entry/good-exit, good-entry/bad-exit, bad-entry, bad-luck (sound decision, adverse outcome), rule-failure, news-misread, stop-too-tight, target-too-greedy
  - what was right, what was wrong, and the evidence for each
  - an optional proposed lesson (condition -> adjustment)
  - counterfactual: what a different stop/target/skip would have done
- Explicitly separate decision quality from outcome luck. A losing trade with sound reasoning is NOT a mistake, and a winning trade with bad reasoning IS one.
- Also review rejected and skipped signals after their outcome is known (missed opportunities and correctly avoided losses).

### B. Lessons store (separate from trades; table + vector index)
Each lesson: id, text, trigger conditions (structured: stock/sector, setup type, indicator ranges, news type, VIX/market regime, time of day, style), recommended action (avoid / reduce confidence by X / tighten stop / require extra confirmation / prefer), evidence (list of trade ids supporting and contradicting), support_count, contradiction_count, win-rate impact, status, created_at, last_validated, expires_at.
Statuses: PROPOSED -> TESTING -> ACTIVE -> RETIRED. A rejected or contradicted lesson is retired, never silently deleted.

### C. Lesson promotion rules (anti-overfitting)
- A new lesson starts PROPOSED and has no effect on trading.
- It moves to TESTING (shadow mode: logged "would have changed the decision" but no real effect) only after support from at least N independent trades (default 5, configurable) across different days.
- It becomes ACTIVE only if replay/backtest on historical data shows it improved results out of sample, AND I approve it in the UI. Nothing becomes ACTIVE automatically.
- Active lessons expire or get re-validated every 30 days and are retired if the evidence weakens. Market regimes change.
- Lessons may adjust confidence, add checks, or tighten stops. They may NEVER change position-size limits, risk limits, the daily-loss halt, or the LIVE gate. Those stay human-controlled.

### D. Using lessons before a trade (pre-trade check)
- Before any order: retrieve matching ACTIVE lessons (structured filters + semantic search on setup description) and pass them to the analysis step as constraints, together with their evidence counts.
- The decision record must list which lessons were consulted, which applied, and how each changed the outcome (e.g. "confidence 82 -> 70, below threshold, REJECTED by lesson L-014").
- TESTING-status lessons are logged as "would have applied" only.
- Also show the 3-5 most similar past trades (setup, outcome, review summary) to the analysis step.

### E. Pattern mining (weekly)
- A weekly job aggregates reviews by setup, stock, sector, news type, time of day, regime, and algo, and surfaces statistically meaningful patterns (with sample sizes and confidence intervals) as PROPOSED lessons. Statistics first, LLM narrative second.

### F. Evals for the learning loop
- Measure: do trades influenced by lessons beat comparable trades without them (A/B via shadow mode and holdout)? Track lesson precision (how often applying it was right), and the number of lessons retired.
- Keep a holdout period/set that lessons are never tuned against.
- Review-quality eval: spot-check the reviewer's classifications against my manual labels, and flag hindsight bias (using later information).

### G. Frontend: "Learning" page
- Per-trade review (what went right/wrong, classification, counterfactual).
- Lessons board by status with evidence, support/contradiction counts, and Approve / Reject / Retire buttons.
- "Lessons applied" panel inside each Decision Log entry.
- Charts: mistake categories over time, win rate with vs without lessons, lesson impact on P&L.
- Weekly digest on Telegram (top mistakes, new proposed lessons awaiting approval).

### H. Safety
- Reviews and lessons are logged with prompt version, model, and cost.
- Max active lessons cap and a token budget for injected context so prompts don't bloat.
- Lessons are data, never instructions. News text can't create or edit lessons, and the retrieval step must treat lesson text as untrusted input (prompt-injection safe).
- Everything works in PAPER first. LIVE uses only lessons validated on paper plus replay.

---

## 16. Deliverables and phases
Monorepo (/backend, /frontend, /evals, /deploy), README, Alembic migrations, seed data, tests for the parser, trade gate, sizing rule, PaperBroker, auth, mode-switch rules, and lesson-promotion rules.

Phases (build one at a time, show results, wait for my approval):
1. DB + backend skeleton + logging + auth (single user, TOTP)
2. PaperBroker + sizing + risk limits + deterministic tests (17E)
3. Algo Builder + backtest + parser evals (17A)
4. News/AI pipeline + replay engine + triage/signal/reasoning evals + model comparison (17B, C, D, F) + post-trade reviewer and lessons tables, PROPOSED only (18A, B)
5. Position monitor + Telegram + online evals and drift alerts (17G) + pre-trade lesson retrieval, TESTING shadow mode, weekly mining (18C, D, E)
6. Frontend: all pages in section 11, including Evals and Learning, plus LIVE gates (17H) and lesson approval workflow
7. Deployment (domain, HTTPS, VPS, backups, CI gate on deploy)

Show the plan first, then build.
