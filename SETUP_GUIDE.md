# Paper Trading Setup Guide (Windows)

This walks you from a fresh machine to a running paper-trading bot. No prior
Python experience assumed. Follow it top to bottom the first time; after that,
the "Daily workflow" section at the end is all you'll need.

**Recommended starting path:** Alpaca + stocks/ETFs (including the oil ETF
`USO`). It's the easiest — web-based keys, free, nothing extra to keep running.
Get comfortable there before adding IBKR (futures/crude) or options, which need
more setup. This guide does Alpaca first, then those as optional add-ons.

Everything here is paper trading — fake money, real market data. Nothing risks
real capital. None of this is financial advice.

---

## What you'll install (checklist)

| # | Software | Why | Required? |
|---|----------|-----|-----------|
| 1 | **Python 3.12** <!-- asof:python-version --> | runs the program | Yes |
| 2 | **The algo-trading code** (the zip) | the program itself | Yes |
| 3 | **Alpaca paper account** (free) | stocks / ETFs / options paper trading | Yes for live paper |
| 4 | **VS Code** (optional) | nicer editing of strategy settings | Optional |
| 5 | **IB Gateway + IBKR paper account** | futures / crude oil paper trading | Optional (later) |

You can do Parts 1–5 (offline backtesting) with just #1 and #2 — no account
needed. You only need Alpaca once you want to place live paper trades (Part 7+).

---

## Part 1 — Install Python

1. Go to **https://www.python.org/downloads/** and download the latest Windows
   installer for **Python 3.12** <!-- asof:python-version --> (3.11, 3.12, or 3.13 all work fine).
2. Run the installer. **CRITICAL:** on the first screen, tick the box
   **"Add python.exe to PATH"** at the bottom before clicking Install. If you
   miss this, the `python` command won't work later.
3. Click "Install Now" and let it finish.
4. Verify it worked: press **Win + R**, type `powershell`, hit Enter, and in the
   window that opens type:
   ```powershell
   python --version
   ```
   You should see something like `Python 3.12.x`. If you instead get
   "python is not recognized," see Troubleshooting → *python not recognized*.

---

## Part 2 — Put the code on your machine

1. Find the `algo-trading.zip` you downloaded.
2. Right-click it → **Extract All…** → choose a location. A simple one:
   `C:\` (which gives you `C:\algo-trading`). Avoid spaces in the path.
3. Open the folder. You should see `run_backtest.py`, `run_paper.py`, a
   `strategies` folder, a `README.md`, etc. This folder is your project.

---

## Part 3 — Open PowerShell in the project folder

PowerShell is the command-line window where you'll type commands. To open it
*already pointed at your project*:

- Open the `C:\algo-trading` folder in File Explorer.
- Click in the address bar, type `powershell`, and press Enter.
  (A PowerShell window opens with its location already set to that folder.)

Alternatively, open PowerShell from the Start menu and navigate manually:
```powershell
C:
cd C:\algo-trading
```
(The `C:` switches to the C drive; `cd` = "change directory.")

To confirm you're in the right place:
```powershell
dir
```
You should see the project files listed.

---

## Part 4 — Create a virtual environment and install dependencies

A "virtual environment" is a private sandbox for this project's Python packages,
so they don't collide with anything else on your system. You create it once.

1. Create it:
   ```powershell
   python -m venv venv
   ```
   This makes a `venv` folder in your project. (Takes a few seconds.)

2. Activate it:
   ```powershell
   .\venv\Scripts\Activate.ps1
   ```
   Your prompt should now start with `(venv)`. That means the sandbox is active.

   **If you get a red error about scripts being disabled**, run this once, then
   re-run the activate line above:
   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   ```
   (This only relaxes the rule for the current window — it's safe and temporary.)

3. Install the libraries the project needs:
   ```powershell
   pip install -r requirements.txt
   ```
   This downloads numpy, pandas, yfinance, alpaca-py, ib_async, statsmodels, and
   matplotlib. It'll take a minute or two and print a lot of text — that's normal.
   When it finishes without red ERROR lines, you're set.

   *Tip:* if pip complains about its own version, run
   `python -m pip install --upgrade pip` and try again.

**Remember:** you must **activate** the environment (step 2) every time you open
a new PowerShell window to work on this. Creating it (step 1) is one-time only.

---

## Part 5 — Verify everything works (offline, no account needed)

Before touching any account, confirm the engine runs. These use built-in
synthetic data and need no internet or keys:

```powershell
# A backtest with a per-trade breakdown:
python run_backtest.py --synthetic --strategy meanrev --trades

# Scan for cointegrated pairs:
python run_screener.py --synthetic

# Walk-forward (out-of-sample) test:
python run_walkforward.py --synthetic --strategy meanrev --train 250 --test 60

# Model-based options backtest:
python run_options_backtest.py --synthetic --strategy momentum
```

If each prints a results table, the whole framework is working. Now let's get
real data and live paper trading.

---

## Part 6 — Create your Alpaca paper account and API keys

1. Go to **https://alpaca.markets/** and sign up for a free account.
2. Once logged in, switch to the **Paper Trading** account. There's a toggle or
   an account switcher in the dashboard (often labeled "Paper" vs "Live"). Make
   sure you are in **Paper** — this is the fake-money environment.
3. Find the **API Keys** panel (usually on the paper dashboard home, in a box
   like "Your API Keys," or under a Keys/API section). Click **Generate** (or
   "Regenerate") to create a key pair.
4. You'll see an **API Key ID** and a **Secret Key**. Copy both immediately —
   the secret is shown only once. If you lose it, just regenerate a new pair.

   > The exact dashboard layout changes from time to time; if the wording
   > differs, look for anything about "API keys" on the paper account.

### Give the keys to the program

The program reads your keys from two environment variables. The simplest
reliable way on Windows:

1. In your project folder, create a file named **`set-keys.ps1`** (you can make
   it in Notepad or VS Code). Put this inside, pasting your real keys:
   ```powershell
   $env:ALPACA_API_KEY    = "PKxxxxxxxxxxxxxxxxxx"
   $env:ALPACA_SECRET_KEY = "your_secret_key_here"
   ```
2. Save it. Then, in each PowerShell session where you'll trade, load it by
   **dot-sourcing** it (note the leading dot and space):
   ```powershell
   . .\set-keys.ps1
   ```
   That sets both keys for the current window.

**Security notes:** keep `set-keys.ps1` private — don't email it, post it, or
commit it to any public repo. These are paper keys so the risk is low, but it's
a good habit. (If you ever push this project to GitHub, add `set-keys.ps1` and
`venv/` to a `.gitignore`.)

---

## Part 7 — Your first real-data backtest

With the environment active, fetch real history and test strategies (this uses
free Yahoo Finance data — no keys required for backtests):

```powershell
# Mean reversion on the oil ETF, last few years, with the trade log:
python run_backtest.py --symbols USO --strategy meanrev --start 2022-01-01 --trades

# Scan an energy basket for tradeable pairs:
python run_screener.py --symbols USO BNO XLE XOM CVX COP --start 2022-01-01

# Walk-forward a strategy on SPY to check it isn't overfit:
python run_walkforward.py --symbols SPY --strategy momentum --start 2018-01-01 --train 252 --test 63
```

Use these to decide what's worth paper-trading. A strategy that looks bad here,
or falls apart in the walk-forward, isn't worth running live.

---

## Part 8 — Run the live paper bot (Alpaca)

**Timing matters:** the US stock market is open **9:30 AM – 4:00 PM Eastern,
Monday–Friday**. Outside those hours the bot will print "market closed; waiting"
and simply idle. Run it during market hours to see it trade.

1. Make sure your environment is active (`(venv)` in the prompt) and your keys
   are loaded (`. .\set-keys.ps1`).
2. Start the bot:
   ```powershell
   python run_paper.py --broker alpaca --symbols USO --strategy meanrev --interval 5Min
   ```
3. What you'll see: every 60 seconds it prints a timestamped "tick." When the
   strategy decides to act, it prints the order it's placing. It's trading fake
   money in your Alpaca paper account — you can watch positions appear on the
   Alpaca dashboard too.
4. To stop it, press **Ctrl + C** in the PowerShell window.

Other examples:
```powershell
# Market-neutral pairs on two oil ETFs (checks every 15 min):
python run_paper.py --broker alpaca --symbols USO BNO --strategy pairs --interval 15Min

# Momentum on SPY, with a tighter daily-loss kill switch (2%):
python run_paper.py --broker alpaca --symbols SPY --strategy momentum --interval 5Min --max-daily-loss 2
```

The bot has a built-in risk kill switch: if the account draws down past
`--max-daily-loss` percent on the day (default 3%), it flattens everything and
halts. Leave it running during market hours and check on it periodically.

---

## Part 9 (optional) — Options paper trading (Alpaca)

Options must be enabled on your Alpaca paper account first:

1. In the Alpaca dashboard, find the options-trading setting for the paper
   account and enable it (there may be a short options-agreement step).
2. With keys loaded, run the directional options bot — it reads a signal on the
   underlying and buys calls/puts to express it:
   ```powershell
   python run_options.py --underlying SPY --strategy momentum --interval 15Min --allow-puts
   ```
   This only ever *buys* options, so your max loss per trade is the premium paid.

Before running it live, get intuition from the model-based options backtest:
```powershell
python run_options_backtest.py --underlying USO --strategy meanrev --start 2020-01-01 --dte 45
```

---

## Part 10 (optional) — Futures / crude oil (IBKR)

Alpaca can't trade futures, so crude (`CL`) goes through Interactive Brokers.
IBKR is more involved because it needs a local "gateway" app running.

1. Open an **Interactive Brokers** account and enable **paper trading**.
2. Download and install **IB Gateway** (lighter) or **Trader Workstation (TWS)**
   from IBKR, and log in to your **paper** account.
3. Enable the API: in the app go to **Global Configuration → API → Settings** and
   tick **"Enable ActiveX and Socket Clients."** Note the port number:
   - Paper TWS: **7497** <!-- asof:ibkr-paper-tws-port -->  •  Paper Gateway: **4002** <!-- asof:ibkr-paper-gateway-port -->
4. Install the library (already in requirements, but if needed): `pip install ib_async`
5. With the gateway app running and logged in, start the bot:
   ```powershell
   python run_paper.py --broker ibkr --symbols CL --strategy momentum --interval 5Min --ib-port 7497 --ib-sectype FUT --ib-exchange NYMEX
   ```
   The adapter finds the front-month crude contract automatically. One CL
   contract controls 1,000 barrels (~$70k <!-- asof:cl-contract-notional --> of oil), so size carefully.

**Key difference from Alpaca:** IBKR has no web keys — the bot connects to the
gateway app over a local socket, so that app must stay open and logged in the
whole time the bot runs.

---

## Troubleshooting

**"python is not recognized as… a command"**
Python isn't on your PATH. Reinstall Python (Part 1) and make sure you tick
"Add python.exe to PATH," or add it manually via the installer's "Modify" option.

**Activating the venv gives a red "scripts is disabled" error**
Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then re-run the
activate command. (Session-only; resets when you close the window.)

**"ModuleNotFoundError: No module named 'pandas'" (or numpy, alpaca, etc.)**
Your virtual environment isn't active or dependencies aren't installed. Confirm
the prompt shows `(venv)`; if not, run `.\venv\Scripts\Activate.ps1`. Then
`pip install -r requirements.txt`.

**"Set ALPACA_API_KEY and ALPACA_SECRET_KEY env vars"**
Your keys aren't loaded in this window. Run `. .\set-keys.ps1` (with the leading
dot). Environment variables only last for the window you set them in.

**"No data fetched" on a backtest**
Check your internet connection and the symbol spelling. Yahoo Finance symbols:
stocks/ETFs are plain (`USO`, `SPY`); futures use `=F` (`CL=F` crude, `ES=F`
S&P). If one symbol has no data, the run stops.

**The paper bot just says "market closed; waiting"**
That's expected outside 9:30 AM–4:00 PM Eastern on weekdays. It'll start acting
when the market opens.

**IBKR bot can't connect**
Make sure IB Gateway/TWS is open and logged into the paper account, the API is
enabled (Part 10 step 3), and the `--ib-port` matches the app's port.

**pip install fails with a build/compiler error**
Run `python -m pip install --upgrade pip` first, then retry. The listed packages
ship prebuilt Windows wheels, so this is rare.

---

## Daily workflow (once you're set up)

Every time you sit down to run the bot:

```powershell
# 1. Open PowerShell in C:\algo-trading (type "powershell" in the folder's address bar)
# 2. Activate the environment:
.\venv\Scripts\Activate.ps1
# 3. Load your keys:
. .\set-keys.ps1
# 4. Run whatever you want, e.g.:
python run_paper.py --broker alpaca --symbols USO --strategy meanrev --interval 5Min
```

That's it. Steps 1–3 take about ten seconds once it's muscle memory.

---

## A sane first-two-weeks plan

1. **Days 1–2:** do Parts 1–7. Run backtests and walk-forwards on the symbols you
   actually care about (start with `USO` for oil). Learn which strategies hold up
   out-of-sample and which don't.
2. **Week 1:** pick one or two strategies that survived the walk-forward, and run
   them on Alpaca paper (Part 8) during market hours. Watch how they behave live
   vs. how the backtest suggested.
3. **Week 2:** add a second asset or the options/IBKR paths if you want. Keep a
   simple log of what each bot did and whether it matched expectations.

Only after weeks of paper results that you understand and trust should you even
think about real money — and then with money you can afford to lose, small size,
and the risk limits turned on. Most retail algo strategies lose money net of
costs; the whole point of this framework is to find that out on paper, for free.

*Not financial advice. Trading involves risk of loss.*
