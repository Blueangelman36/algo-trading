# algo-trading

[![CI](https://github.com/Blueangelman36/algo-trading/actions/workflows/ci.yml/badge.svg)](https://github.com/Blueangelman36/algo-trading/actions/workflows/ci.yml)
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/Blueangelman36/algo-trading)

A systematic trading framework with three pieces that share the same strategy code:

1. **Backtester** — event-driven, models commission + slippage, asset-agnostic.
2. **Live paper-trading bot** — Alpaca or IBKR paper account, same strategies, with risk controls.
3. **Web dashboard** — a single-user UI for running research and managing bots on
   your own server (see **WEB_DASHBOARD.md**).

> **Try it without installing anything:** click *Open in GitHub Codespaces* above,
> wait for the terminal, and run `python run_backtest.py --synthetic --strategy meanrev`.

The design rule: a strategy emits **target weights** (`+1` long, `0` flat, `-1` short)
per symbol. The engine — backtest *or* live — turns those into orders. So the exact
strategy you backtest is the one you paper-trade. No logic rewrite between them.

> **New here / on Windows?** Follow **SETUP_GUIDE.md** — a step-by-step,
> no-experience-assumed walkthrough from installing Python to a running paper bot.
> The notes below are the quick version.

---

## Asset-class reality (read this first)

No single retail broker covers everything. The framework is built around that:

| Asset class                    | Backtest data            | Live broker            |
|--------------------------------|--------------------------|------------------------|
| Stocks / ETFs                  | yfinance (`AAPL`,`SPY`)  | **Alpaca** (built in)  |
| Oil / commodities via ETF      | yfinance (`USO`,`USL`)   | **Alpaca** (built in)  |
| Options                        | (chain snapshots)        | **Alpaca** (built in)  |
| Futures (crude `CL`, S&P `ES`) | yfinance `CL=F` (rough)  | **IBKR** (built in)    |

Live trading goes through a `Broker` interface with two adapters: **Alpaca**
(`live/alpaca_bot.py`) for stocks/ETFs, and **IBKR** (`live/ibkr_bot.py`) for
futures/commodities like crude. The strategy code and the live loop
(`live/trader.py`) are identical across both — only the broker changes.

---

## Setup (Windows, step-by-step)

You're on Windows — these are the consolidated commands, using the C: drive. Run them
in PowerShell.

**1. One-time setup**
```powershell
# Get the code (or download the ZIP from GitHub and unzip it to C:\algo-trading):
cd C:\
git clone https://github.com/Blueangelman36/algo-trading.git
cd C:\algo-trading

# Create and activate a virtual environment:
python -m venv venv
.\venv\Scripts\Activate.ps1
# If activation is blocked, allow scripts for this session only:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   then re-run the activate line above.

# Install dependencies:
pip install -r requirements.txt
```

**2. Run a backtest (no account or keys needed)**
```powershell
# Offline smoke test on generated data:
python run_backtest.py --synthetic --strategy meanrev

# Real data — mean reversion on an oil ETF:
python run_backtest.py --symbols USO --strategy meanrev --start 2022-01-01

# Momentum on crude + S&P futures, save an equity-curve chart:
python run_backtest.py --symbols CL=F ES=F --strategy momentum --start 2022-01-01 --plot equity.png

# Show the per-trade table and export every round-trip trade to CSV:
python run_backtest.py --symbols USO --strategy meanrev --start 2022-01-01 --trades --trades-csv oil_trades.csv
```

**2b. Find tradeable pairs before pairs-trading**
```powershell
# Offline demo (one real pair hidden in noise):
python run_screener.py --synthetic

# Scan an energy basket for cointegrated pairs, save the ranking:
python run_screener.py --symbols USO BNO XLE XOM CVX COP --start 2022-01-01 --csv pairs.csv
```
Take a winner from the screen and backtest it as a pair:
```powershell
python run_backtest.py --symbols USO BNO --strategy pairs --start 2022-01-01 --trades
```

**2c. Walk-forward test (guard against overfitting)**

A normal backtest tunes and judges parameters on the *same* history, which
flatters them. Walk-forward optimizes parameters on a train window, then scores
them on the *next* unseen window, and repeats — the stitched out-of-sample curve
is the honest result. Watch the in-sample vs out-of-sample gap it reports.
```powershell
python run_walkforward.py --synthetic --strategy meanrev --train 250 --test 60
python run_walkforward.py --symbols SPY --strategy momentum --start 2018-01-01 --train 252 --test 63 --plot wf.png
```

**2d. Options backtest (model-based)**

Tests the directional options idea on history. It prices options with
Black-Scholes off the underlying's real prices and an estimated volatility —
NOT real historical option quotes (those aren't freely available). Read it for
theta/delta intuition and your DTE/moneyness choices, not precise P&L.
```powershell
python run_options_backtest.py --synthetic --strategy momentum
python run_options_backtest.py --underlying USO --strategy meanrev --start 2020-01-01 --dte 45
```

**3. Set up live paper trading (fake money, real market)**

*Option A — Alpaca (stocks / ETFs / oil ETFs):*
```powershell
# a) Make a free account at https://alpaca.markets and generate PAPER api keys.
# b) Set them as environment variables for this PowerShell session:
$env:ALPACA_API_KEY    = "your_paper_key"
$env:ALPACA_SECRET_KEY = "your_paper_secret"

# c) Run the paper bot (checks every 60s during market hours):
python run_paper.py --broker alpaca --symbols SPY --strategy meanrev --interval 1Min

# Pairs trading two oil ETFs, market-neutral:
python run_paper.py --broker alpaca --symbols USO BNO --strategy pairs --interval 15Min
```

*Option B — IBKR (futures / crude oil):*
```powershell
# a) Open an IBKR account, enable PAPER trading.
# b) Install and launch IB Gateway (or TWS), log into the PAPER account.
# c) Enable the API: Global Config -> API -> Settings ->
#    check "Enable ActiveX and Socket Clients". Note the port
#    (paper TWS = 7497, paper Gateway = 4002).
# d) Run the bot against crude futures (CL trades on NYMEX):
python run_paper.py --broker ibkr --symbols CL --strategy momentum --interval 5Min `
    --ib-port 7497 --ib-sectype FUT --ib-exchange NYMEX
```
IBKR uses no web keys — the bot connects to the local Gateway/TWS over a socket, so
that app must be running and logged in. The adapter auto-resolves the front-month
contract for you. One CL contract = 1,000 barrels, so size carefully.

Leave it running during market hours and watch it trade fake money. `Ctrl-C` stops it.

*Option C — Alpaca options (defined-risk long calls/puts):*
```powershell
# Needs Alpaca paper keys (as in Option A) with options enabled on the account.
# Takes a directional signal on the UNDERLYING and buys ATM options to express it:
python run_options.py --underlying SPY --strategy momentum --interval 15Min --allow-puts

# Calls only on the oil ETF, ~45 DTE, 2 contracts:
python run_options.py --underlying USO --strategy meanrev --dte 45 --qty 2
```
This bot only ever BUYS options (long premium), so your max loss per position is the
premium paid — no naked shorting or assignment risk. It holds one option at a time and
auto-closes as expiry approaches. Options decay and can expire worthless even when your
direction is right but timing is off. Not financial advice.

---

## What each strategy does

- **Mean reversion** (`meanrev`): fades stretched prices using a z-score / Bollinger
  band. Profits in range-bound, choppy markets; bleeds in strong trends.
- **Momentum** (`momentum`): rides trends via moving-average crossover + optional
  breakout. The mirror image — profits in trends, chops up in ranges.
- **Pairs trading** (`pairs`, needs exactly 2 symbols): market-neutral stat-arb.
  Trades the spread between two cointegrated instruments (e.g. two oil ETFs), going
  long the cheap leg and short the rich leg when they diverge, closing when they
  converge. Low return *and* low drawdown — it doesn't bet on market direction.
  **Only works if the pair is genuinely cointegrated** — test the spread first
  (statsmodels `coint`), or it can diverge forever.

Tune them by editing the constructor params in `strategies/`.

---

## How to read the results (don't skip)

Every backtest now prints two blocks: portfolio-level metrics and a
**trade-by-trade** report (round-trip trades matched from fills).

Portfolio level:
- **Max drawdown** is what blows up accounts — weight it above headline return.
- **Sharpe** below ~1 is weak; a backtested Sharpe above ~2–3 usually means you
  **overfit** (curve-fitting to the past).
- Commission + slippage are modeled. Re-run with `--commission 0` to see how much of
  your "edge" is just ignoring costs.

Trade by trade (`--trades` shows the table, `--trades-csv` exports all of them):
- **Win rate** alone is a trap. A 30%-win-rate trend strategy can be very profitable
  if its winners dwarf its losers; an 80%-win-rate strategy can bleed if the rare
  losses are huge. Read it together with the next two.
- **Profit factor** = gross wins ÷ gross losses. Above 1.0 is profitable; ~1.5+ is
  healthy; suspiciously high usually means overfit.
- **Expectancy/trade** is the average $ you make per trade after costs — the single
  most honest number. Negative expectancy means the strategy loses, full stop.
- **Win/loss ratio** (avg win ÷ avg loss) tells you the shape: mean reversion tends
  to win often by small amounts; momentum wins rarely but big.

A great result from **few trades** is noise, not edge — check the trade count first.

The trade report reconciles exactly to the equity curve (sum of every trade's net P&L,
including any position still open at the end marked to last price, equals total P&L), so
the numbers are internally consistent, not approximations.

The honest workflow: backtest → paper-trade for **weeks** → only then consider real
money, with strict risk limits. Most retail algo strategies lose money net of costs;
the engine is built to show you that *before* you fund it, not after.

---

## Project layout
```
algo-trading/
  run_backtest.py        # CLI: backtests
  run_screener.py        # CLI: scan a basket for cointegrated pairs
  run_walkforward.py     # CLI: walk-forward (out-of-sample) analysis
  run_paper.py           # CLI: live paper trading (Alpaca / IBKR)
  run_bot.py             # launches run_paper.py from a bots/<name>.json config
  run_options.py         # CLI: directional options bot (Alpaca, live paper)
  run_options_backtest.py# CLI: model-based options backtest
  run_filter_ab.py       # CLI: A/B the catalyst filters on history *
  run_news_fetch.py      # CLI: build an offline Alpaca news cache *
  sizing.py              # volatility-targeted sizing (backtest and live)
  strategies/
    base.py              # Strategy interface (shared everywhere)
    mean_reversion.py
    momentum.py
    pairs.py             # market-neutral stat-arb (2 symbols)
    breakout.py          # Donchian channel breakout
    pyramiding_breakout.py # Turtle-style scale-in
    xsec_momentum.py     # cross-sectional momentum (3+ symbols)
    regime.py            # efficiency-ratio regime filter (wrapper)
    news_filter.py       # catalyst vetoes: news, EIA/OPEC, earnings, SEC (wrapper)
  backtest/
    engine.py            # event loop
    portfolio.py         # cash, positions, commission, slippage
    metrics.py           # Sharpe, drawdown, Calmar, exposure (+ compute_series)
    trades.py            # round-trip trade matching + win-rate stats
    walkforward.py       # in-sample optimize -> out-of-sample test, rolling/anchored
    blackscholes.py      # option pricing + greeks (stdlib only)
    options_engine.py    # model-based options backtester
  research/
    screener.py          # cointegration tests (Engle-Granger, ADF, half-life, hedge drift)
    event_calendar.py    # EIA / OPEC event calendar
    earnings_calendar.py # earnings dates (yfinance)
    sec_filings.py       # SEC EDGAR 8-K feed
  data/
    loader.py            # yfinance + synthetic generators
  live/
    broker_base.py       # Broker interface
    trader.py            # broker-agnostic live loop + risk manager
    alpaca_bot.py        # Alpaca adapter (stocks / ETFs)
    ibkr_bot.py          # IBKR adapter (futures / crude) via ib_async
    alpaca_options.py    # Alpaca options: discovery, selection, quotes, orders
  webapp/                # Flask dashboard (WEB_DASHBOARD.md)
  deploy/                # systemd unit + sudoers rule for the dashboard
  tools/claims.py        # reads documented numbers from the code, for asof
```
\* These two need `data/news.py` (the Alpaca news client), which is not in this
repository yet.

## Guides

| Guide | For |
|---|---|
| **SETUP_GUIDE.md** | First run on Windows, no experience assumed |
| **CLOUD_DEPLOYMENT.md** | Running a bot 24/7 on a small DigitalOcean server |
| **WEB_DASHBOARD.md** | Installing and using the dashboard on that server |
| **IMPLEMENTATION_GUIDE.md** | The v2 upgrade: what changed, and how to roll it out |

---

## Contributing

Two checks keep this repository honest, and CI runs both on every push:

- **[chesterton](https://github.com/Blueangelman36/chesterton)** (`fence`)
  remembers *why* code exists. Guards that look removable but are not (the
  sudo bot-name check, the daily risk re-anchor, the vetoes that never block an
  exit) have recorded reasons in `.fence/`, and a commit that deletes one is
  stopped with the reason quoted. Ask with `fence why <file>`.
- **[asof](https://github.com/Blueangelman36/asof)** keeps the numbers in these
  docs true. A marked number is checked against the code that decides it, or
  carries a shelf life if only a person can check it (prices, OPEC dates). A
  weekly job opens an issue when one is overdue. Ask with `asof why <name>`.

```powershell
pip install -r requirements-dev.txt   # the app's requirements plus both tools
fence init                            # once per clone: the pre-commit hook
asof check                            # are the docs still true?
```

**AGENTS.md** has the same rules for coding agents.

## License

MIT. See **LICENSE**.

---

*Not financial advice. Trading involves risk of loss. Paper-trade and review the code
before risking real capital.*
