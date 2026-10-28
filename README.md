# S&P 500 swing playbook

Overnight research job: screen the S&P 500 after the US close, keep up to three longs and three shorts, and print **exact** next-session entry/exit instructions. A second run with those tickers as arguments waits for the NYSE open, checks the gap, then places surviving brackets in **TWS / LYNX Gateway**.

This is not financial advice.

## What you need

### Required for AI overlay

1. An [xAI](https://console.x.ai) account with billing enabled.
2. An API key in `.env` as `XAI_API_KEY` (copy `.env.example`).

Grok uses server-side `web_search` and `x_search`. You do **not** need a separate X/Twitter developer account. Overnight cron does not need Cursor.

Default model: `grok-4.3` (cheaper than `grok-4.6`). The model is only called on the **top five longs and top five shorts**. Abort if estimated spend would exceed `$2` (configurable). A full ten-name shortlist can exhaust that budget before the last names are reviewed.

### Free data (no account)

- **Yahoo Finance** via `yfinance` — daily OHLCV and next earnings dates.
- **Wikipedia** — current S&P 500 membership and GICS sector.

### Optional free keys (not required)

- [Tiingo](https://www.tiingo.com) — `TIINGO_API_KEY` reserved for a later EOD fallback.
- [Financial Modeling Prep](https://site.financialmodelingprep.com) — `FMP_API_KEY` reserved for a later constituent/fundamentals fallback.

If Yahoo becomes unreliable, Tiingo Power (~$10–30/mo) is the intended paid upgrade. Skip Polygon/Massive for this daily workflow.

### Lynx / TWS (second run only)

Retail **TWS or LYNX Gateway** + socket API. Enable API (Global Configuration → API → Settings), trust `127.0.0.1`. If TWS shows **Accept incoming connection attempt**, click Yes or the handshake stops. Default port is **paper 7497**; pass `--live` for 7496. Client ID defaults to 17; another API window holding that ID will block connect. Paper login is TWS / Lynx Trading App, not LYNX+. Shorts need a **margin** account. Do not use FIX, the Client Portal Web API, IBALGOs, or fractional shares.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

Quant-only (no xAI tokens):

```bash
swingtrading run --skip-ai
```

Full run (needs `XAI_API_KEY`):

```bash
swingtrading run
```

Refresh the Parquet cache only:

```bash
swingtrading refresh
```

Useful flags: `--as-of YYYY-MM-DD`, `--offline` (cache only), `--risk-usd 500`.

Writes `out/YYYY-MM-DD.json` and `out/YYYY-MM-DD.md`.

## Execute (second run, check the gap then send)

Pass `--all` to use every symbol on the saved shortlist, or pass tickers yourself. You can start TWS and the job **before** 9:30 ET. The job connects (and prompts you to start TWS if needed), waits for the NYSE cash open, checks each name against the opening print, then transmits only the DAY parent + GTC stop/target brackets that still pass. It does **not** park untransmitted orders before the bell.

```bash
# Every name on the latest playbook shortlist
swingtrading run --all

# Or pass symbols yourself
# Paper TWS on 7497; reuse risk from the first run
# Safe to start before 9:30 ET — waits, then gap-checks, then sends
swingtrading run SJM HUBB VRSN --max-new-positions 2

# Override size for this session
swingtrading run SJM HUBB --risk-usd 400 --max-new-positions 1

# Skip the 9:30 wait (already late, or you want the current quote only)
swingtrading run SJM HUBB --now
```

`--risk-usd` on the second run is optional; if omitted, the value stored in the playbook is used. `--max-new-positions` is the cap on **new fills**. When one side already has more transmitted or reused orders, the next check is a name on the other side. If the counts are equal, the higher confidence wins. Buying power is split in half. Each half keeps a 5% buffer, and open playbook positions already consume their side. A new order is sent only when that side has enough left. Manual positions are not assigned to either side. A gap skip or a name that does not fit its side does **not** use up that side's slot: the next name is tried. If that many parents fill, leftover working entries are canceled.

A name is skipped if the open/last gaps through the stop, is already extended through `skip_if`, is already at the target, or if no opening print arrives within 15 minutes after 9:30. If the socket drops while waiting, it reconnects and does not place duplicates.

Parent fills are written to `out/positions.json` with an `orderRef` of `swing:YYYY-MM-DD:SYMBOL`. That ledger is the only thing the time-stop job may close.

If a ledger row is already on session 5, the second run stays connected until **15:45 ET** and submits a qty-scoped **MOC** (not the whole TWS position). Use `--skip-moc` to return after entries instead.

```bash
# Daily 15:45 ET job (days you do not re-run execute)
TZ=America/New_York swingtrading moc

# Flatten due rows now (tests / already in the MOC window)
swingtrading moc --now
```

`--now` on `moc` skips the 15:45 wait and the cutoff check. Manual Lynx positions are never scanned.

Writes `out/YYYY-MM-DD.execution.json`, `.execution.md`, and `out/positions.json`.

## Daily timers

Two user systemd timers. Both run on weekdays and use America/New_York, so the clock stays on New York time when the machine is set to another zone. Arguments come from the `overnight` and `trading` files in the repo root. stdout and stderr go to the user journal.

| Timer | When | Command file |
| --- | --- | --- |
| `swingtrading-overnight.timer` | Mon–Fri 06:00 America/New_York | `overnight` |
| `swingtrading-trading.timer` | Mon–Fri 15:00 America/New_York | `trading` |

The trading unit sets `SWINGTRADING_UNATTENDED=1`. If TWS is not accepting the socket, the journal gets `TWS is not running at 127.0.0.1:7496. Retrying in 60 seconds.` Once the cash session is over (16:00 America/New_York, or any time on a weekend), it logs `The cash session is over; giving up.` and the service exits. A missed 15:00 start is not replayed later, so a machine that wakes up after the close does not send orders. A missed 06:00 screen does run at the next boot.

Install once, from the repo root:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/swingtrading-overnight.service systemd/swingtrading-overnight.timer \
   systemd/swingtrading-trading.service systemd/swingtrading-trading.timer \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now swingtrading-overnight.timer swingtrading-trading.timer
loginctl enable-linger "$USER"
```

Linger lets the 06:00 screen run when you are logged out. TWS itself still has to be logged in before 15:00; the trading job only connects to it.

```bash
systemctl --user list-timers 'swingtrading-*'
journalctl --user -u swingtrading-overnight.service -u swingtrading-trading.service -f
```

## How names are chosen

1. Ingest constituents, ~2y daily bars, earnings dates (cached as Parquet).
2. Hard filters: 20-day dollar volume, history length, earnings in the next two sessions.
3. Score continuation and mean-reversion setups, long and short. A symbol is kept on its better side, and only if that score is at least 35.
4. Give Grok the top five longs and the top five shorts (max two per GICS sector on each side). A side with fewer names above 35 is not padded.
5. Build ATR trade plans: stop 1.25×ATR, target 2R, time stop five sessions, DAY orders, whole shares from `$` risk / stop distance. Grok **cannot** change prices.
6. Optional Grok news + X overlay: veto or shift confidence only.
7. Report at most three surviving longs and three surviving shorts, ranked by final confidence.

## Tests

```bash
pytest
```
