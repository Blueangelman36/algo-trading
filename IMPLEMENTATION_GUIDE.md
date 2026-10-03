# Full Implementation Guide — Trading Framework v2 Upgrade

Everything from this round of work, in deployment order: framework fixes
(risk manager, sizing, engine), five new strategy modules, the catalyst-filter
fixes, dashboard v2, and the A/B validation tooling.

**Assumed setup** (from `CLOUD_DEPLOYMENT.md`): DigitalOcean droplet at
`YOUR_IP`, user `trader`, project at `/home/trader/algo-trading`, bots as
`trading-bot@<name>` systemd units, dashboard as the `dashboard` unit reached
via SSH tunnel. Substitute your own address and user throughout.

**Environments used below:**
- 🖥 **Desktop** = PowerShell on your Windows machine (`C:\algo-trading`)
- 🌐 **Server** = SSH session on the droplet

**Golden rules for this rollout (same as always):**
1. One command at a time over SSH — no multi-line pastes into nano.
2. `python -m py_compile` everything before restarting any service.
3. The sudoers update (Phase 5) must land **before** you touch the new
   dashboard Start/Stop buttons.
4. Everything stays paper. Nothing here touches live keys.

---

## 0. What you're deploying (summary of changes)

### Bug fixes that affect the live bot
| Fix | File | Why it matters |
|---|---|---|
| Risk manager now resets **daily** | `live/trader.py` | Old code anchored equity at launch — a 24/7 bot was enforcing a since-launch drawdown limit, not a daily one. Halts also clear at the day roll now. |
| Daily-bars history window | `live/alpaca_bot.py` | Old code sized the request in minutes; `1Day` lookbacks returned ~1 bar. |
| Vetoed flips now **flatten** | `strategies/news_filter.py` | Old code held the OLD position through the catalyst when a flip was vetoed — violating "vetoes never block exits". |
| Live veto freshness (TTL) | `research/sec_filings.py`, `research/earnings_calendar.py` | Old code cached filings/earnings at startup forever; a weeks-old bot never saw a new 8-K. Now refreshed every 6h / daily. |
| EIA holiday shift generalized | `research/event_calendar.py` | Old code only shifted for Monday holidays; EIA delays for any Mon–Wed holiday (e.g. Veterans Day 2026 is a Wednesday). Stale-OPEC warning now actually prints. |
| News cache truncation | `run_news_fetch.py` | Old code silently capped at 50 articles per 30-day window — exactly the busy months the filter needs. Now paginates. |
| Dashboard: sessions, blocking research, zombie units | `webapp/`, `deploy/` | Random logouts with 2 workers; research froze the UI for up to 15 min; deleted bots restart-looped after reboot. All fixed. |

### New capabilities
- **Strategies:** `breakout` (Donchian), `pyramid` (Turtle-style scale-in),
  `xsec` (cross-sectional momentum, 3+ symbols), plus the `RegimeFilter`
  wrapper (efficiency-ratio veto).
- **Volatility-targeted sizing** (`sizing.py`) — shared by backtest and live,
  identical math. Per-bot `vol_target` config key.
- **Leverage switch, OFF:** `max_gross_leverage=1.0` in engine and
  RiskManager. Raising it later is a single deliberate change; nothing in the
  dashboard exposes it. Leave it.
- **Backtest honesty tools:** `fill_mode="next_open"`, Calmar + exposure
  metrics, walk-forward parameter-stability report, screener `hedge_drift`.
- **Dashboard v2:** async research jobs, live status pills, Edit button,
  catalysts panel, log auto-refresh with VETO/RISK-HALT highlighting, CSRF +
  login throttle, persistent Start/Stop semantics.
- **IBKR term structure:** `IBKRBroker.term_structure("CL")` — contango /
  backwardation snapshot for the future USO veto.
- **A/B tool:** `run_filter_ab.py` proves (or disproves) the catalyst
  filters' value on your real history.

### Behavior changes to be aware of
- **Start/Stop buttons are persistent now.** Start = `enable --now`
  (bot returns after reboot). Stop = `disable --now` (stays stopped).
- **Risk limit re-anchors daily.** First day after deploying, the effective
  limit is tighter than what the old buggy version was enforcing. That's the
  fix working.
- **Old bot configs run unchanged.** Every new config key is optional and
  gated; `uso-meanrev.json` produces identical behavior until you opt in.

---

## 1. File map — what goes where

All paths relative to the project root (`C:\algo-trading` locally,
`/home/trader/algo-trading` on the server).

```
REPLACED (framework)                NEW (framework)
  live/trader.py                      sizing.py                (project root)
  live/alpaca_bot.py                  strategies/breakout.py
  live/ibkr_bot.py                    strategies/pyramiding_breakout.py
  backtest/engine.py                  strategies/regime.py
  backtest/portfolio.py               strategies/xsec_momentum.py
  backtest/metrics.py                 run_filter_ab.py         (project root)
  backtest/walkforward.py
  backtest/options_engine.py        REPLACED (catalyst filters)
  screener.py  (wherever yours lives)  strategies/news_filter.py
                                       research/event_calendar.py
REPLACED (launchers)                   research/earnings_calendar.py
  run_paper.py   ← MERGE POINT!        research/sec_filings.py
  run_bot.py                           run_news_fetch.py

REPLACED (dashboard)                REPLACED (deploy)
  webapp/app.py                       deploy/dashboard.service
  webapp/templates/base.html          deploy/sudoers-trading
  webapp/templates/bots.html
  webapp/templates/login.html       DOCS
  webapp/templates/logs.html          WEB_DASHBOARD.md
  webapp/templates/research.html      IMPLEMENTATION_GUIDE.md (this file)
```

**Untouched:** `strategies/base.py`, `strategies/mean_reversion.py`,
`strategies/momentum.py`, `strategies/pairs.py`, `loader.py`,
`backtest/blackscholes.py`, `backtest/trades.py`, `data/news.py`,
`run_backtest.py`, `run_walkforward.py`, `run_screener.py`, your `bots/*.json`
configs, `alpaca.env`, `dashboard.env`, and `deploy/trading-bot@.service`.

---

## 2. Phase 1 — Local install and validation 🖥

Do everything locally first. If it breaks, it breaks on Windows where nothing
is running.

**2.1 Back up your local project:**
```powershell
Copy-Item C:\algo-trading -Destination C:\algo-trading-backup-$(Get-Date -Format yyyyMMdd) -Recurse
```

**2.2 Extract the package.** With `trading-v2-update.zip` saved at `C:\`:
```powershell
Expand-Archive -Path C:\trading-v2-update.zip -DestinationPath C:\algo-trading -Force
```
The zip mirrors the project layout, so every file lands exactly where the
file map says. (If your `screener.py` lives in a subfolder like `research\`,
move it there after extracting.)

**2.3 THE ONE MANUAL MERGE — `run_paper.py`.** The new file defines the four
catalyst flags but contains a loudly marked block:
```
# ======================= MERGE POINT ================================
```
Open it and your **old** `run_paper.py` (from the backup) side by side, and
replace the MERGE POINT block's guess with your existing catalyst-filter
wiring (the block from `DEPLOY_CATALYST_FILTERS.md` Part 5b). The flags feed
it as `args.news_filter`, `args.event_blackout`, `args.earnings_blackout`,
`args.sec_blackout`. If you skip this, a filter-enabled bot fails loudly with
a clear message — it will not silently ignore the flags.

**2.4 Compile-check everything:**
```powershell
cd C:\algo-trading
python -m py_compile sizing.py run_paper.py run_bot.py run_filter_ab.py run_news_fetch.py
python -m py_compile live\trader.py live\alpaca_bot.py live\ibkr_bot.py
python -m py_compile backtest\engine.py backtest\portfolio.py backtest\metrics.py backtest\walkforward.py backtest\options_engine.py
python -m py_compile strategies\breakout.py strategies\pyramiding_breakout.py strategies\regime.py strategies\xsec_momentum.py strategies\news_filter.py
python -m py_compile research\event_calendar.py research\earnings_calendar.py research\sec_filings.py
python -m py_compile webapp\app.py
```
Every line should print nothing. Any traceback = fix before continuing.

**2.5 The single most important validation — next-open fills on your live
strategy.** Run your USO mean-rev backtest twice and compare:
```powershell
python run_backtest.py --symbols USO --strategy meanrev --start 2023-01-01 --trades
```
Then edit the `BacktestEngine(...)` call in `run_backtest.py` to pass
`fill_mode="next_open"` and run it again. If the edge survives next-open
fills, it's much more likely real. If it evaporates, you've learned something
important **before** deploying anything.

**2.6 Optional local sanity runs:**
```powershell
# New strategies through the walk-forward gate:
python run_walkforward.py --symbols SPY --strategy breakout --start 2020-01-01 --train 252 --test 63
# Catalyst A/B on the live bot's config:
python run_filter_ab.py --symbols USO --strategy meanrev --start 2023-01-01 --events
```

---

## 3. Phase 2 — Upload the package to the droplet 🖥

Two files instead of eight scp commands. The zip is the original package;
`run_paper.py` is YOUR merged copy from step 2.3 (the zip's copy still has
the unmerged MERGE POINT block, so your merged one must ride along):

```powershell
scp C:\trading-v2-update.zip trader@YOUR_IP:/home/trader/
scp C:\algo-trading\run_paper.py trader@YOUR_IP:/home/trader/run_paper.merged.py
```

Both land in the home directory — **nothing in the project is overwritten
yet.** Extraction happens in the next phase, deliberately AFTER the server
backup.

---

## 4. Phase 3 — Server-side backup, extract, and verify 🌐

```powershell
ssh trader@YOUR_IP
```
Then, one at a time:
```bash
cd ~/algo-trading
```
Back up the CURRENT (pre-upgrade) state — this must happen before extraction:
```bash
tar -czf ~/algo-backup-$(date +%Y%m%d).tar.gz --exclude=venv --exclude=.edgar_cache .
```
Now extract the package over the project (this is the moment files change):
```bash
unzip -o ~/trading-v2-update.zip -d ~/algo-trading
```
(If `unzip` isn't installed: `sudo apt install -y unzip` first.)

Put your merged `run_paper.py` in place — it must overwrite the zip's
unmerged copy:
```bash
cp ~/run_paper.merged.py ~/algo-trading/run_paper.py
```
If your `screener.py` lives in a subfolder rather than the project root,
move the extracted one there now.
```bash
source venv/bin/activate
```
Compile-check on the server too (different Python, different files present):
```bash
python -m py_compile sizing.py run_paper.py run_bot.py run_filter_ab.py webapp/app.py
```
```bash
python -m py_compile live/trader.py live/alpaca_bot.py backtest/engine.py strategies/news_filter.py research/event_calendar.py research/sec_filings.py research/earnings_calendar.py
```
Confirm the merge landed (both must return ≥ 1):
```bash
grep -c "event-blackout" run_paper.py
```
```bash
grep -c "vol-target" run_paper.py
```
Confirm all modules import together:
```bash
python -c "import sizing, run_bot; import live.trader, live.alpaca_bot, backtest.engine, backtest.walkforward; import strategies.breakout, strategies.pyramiding_breakout, strategies.regime, strategies.xsec_momentum, strategies.news_filter; import research.event_calendar, research.earnings_calendar, research.sec_filings; print('all imports OK')"
```

---

## 5. Phase 4 — Update the sudoers rule (BEFORE the dashboard) 🌐

The new dashboard's Start/Stop use `enable --now` / `disable --now`, which
the old sudoers file doesn't permit. Do this first or the buttons fail.

```bash
sudo cp ~/algo-trading/deploy/sudoers-trading /etc/sudoers.d/trading
```
```bash
sudo chmod 0440 /etc/sudoers.d/trading
```
```bash
sudo visudo -c
```
**Must print "parsed OK".** If it doesn't:
```bash
sudo rm /etc/sudoers.d/trading
```
immediately — a broken sudoers file can lock you out of sudo — then compare
the file against `deploy/sudoers-trading` for a copy error and retry.

Verify the new permissions work:
```bash
sudo -n systemctl enable --now trading-bot@uso-meanrev.service
```
(no password prompt, no error = good; the bot was likely already running).

---

## 6. Phase 5 — Restart the dashboard on the new service 🌐

```bash
sudo cp ~/algo-trading/deploy/dashboard.service /etc/systemd/system/
```
```bash
sudo systemctl daemon-reload
```
```bash
sudo systemctl restart dashboard
```
```bash
systemctl status dashboard --no-pager
```
Expect **active (running)** with gunicorn showing
`--worker-class gthread --workers 1 --threads 8`. If it failed:
```bash
journalctl -u dashboard -n 40 --no-pager
```
(usual causes: `DASHBOARD_PASSWORD` missing from `dashboard.env`, or a
template copy error — the traceback names the file).

---

## 7. Phase 6 — Dry-run, then restart the live bot 🌐

Never hand systemd an untested change. Dry-run the exact config first:
```bash
cd ~/algo-trading && set -a && source alpaca.env && set +a
```
```bash
python run_bot.py uso-meanrev
```
Healthy output:
```
Starting bot 'uso-meanrev': run_paper.py --broker alpaca --strategy meanrev ...
Catalyst filters active: NewsFiltered(MeanReversion)
LIVE (paper) NewsFiltered(MeanReversion) on ['USO'] @ 5Min, every 60s.
[HH:MM:SS] tick
```
`Ctrl+C` to stop. If it crashed, the traceback points at the problem — fix it
here, not in the service. Then:
```bash
sudo systemctl restart trading-bot@uso-meanrev
```
```bash
sudo journalctl -u trading-bot@uso-meanrev -f --no-pager
```
Watch a few ticks, then `Ctrl+C` out of journalctl (the bot keeps running).

---

## 8. Phase 7 — Verify through the browser 🖥

Open the tunnel and keep that window open:
```powershell
ssh -L 8000:127.0.0.1:8000 trader@YOUR_IP
```
Browse to **http://127.0.0.1:8000** <!-- asof:dashboard-port --> and walk this checklist:

- [ ] Log in. Refresh a few times — **no random bounce back to login**.
- [ ] Bots table shows Name / Symbols / Strategy / **Interval** / Sizing /
      Filters / Status, correctly aligned.
- [ ] Wait ~10s: status pills refresh on their own.
- [ ] **Upcoming catalysts panel** shows this week's EIA report (Wednesdays
      10:30 ET; Thursday on holiday weeks). No red OPEC-stale banner (that
      appears after 2026-11-29 <!-- asof:opec-last --> until you refresh `OPEC_DATES`).
- [ ] Hit **Edit** on `uso-meanrev` — the form fills with its config.
- [ ] Research tab → run a screener → spinner appears, page stays responsive,
      output fills in when done. Refresh mid-run: the job survives.
- [ ] Logs tab → select the bot → lines append every 5s <!-- asof:log-refresh-seconds -->; wait for a Wednesday
      10:30 ET to see a VETO line highlighted amber.
- [ ] Stop the bot → `sudo systemctl is-enabled trading-bot@uso-meanrev` on
      the server says **disabled**. Start it → **enabled**. (This is the new
      persistent semantics working.)

Next-day check (one-time): the first tick after midnight, the journal shows
the risk manager re-anchoring — and if a halt was ever active, it clears.

---

## 9. Phase 8 — Prove the pieces with research 🌐

None of the new strategies or filters goes live without surviving the same
gate as everything else. In order of value:

**9.1 Catalyst A/B on your actual live config:**
```bash
python run_filter_ab.py --symbols USO --strategy meanrev --start 2023-01-01 --events
```
Good = fewer trades, smaller worst losses / max drawdown, similar returns.
The script prints a verdict using exactly those criteria. Zero vetoes = the
window had no catalysts hitting your entries; try a longer window.

**9.2 New strategies through the walk-forward gate** (they're registered as
`breakout`, `pyramid` won't grid-search until you add a grid, `xsec`):
```bash
python run_walkforward.py --symbols SPY --strategy breakout --start 2019-01-01 --train 252 --test 63
```
Read the new **parameter stability** section at the bottom: a mode share
below 50% means the optimizer is chasing noise — distrust the result even if
OOS looks fine.

**9.3 Vol targeting A/B:** wire `sizer=VolatilityTargeting(...)` into a
`run_backtest.py` run (3 lines, pattern in `sizing.py`'s docstring) and
compare vol/drawdown against the fixed-sizing baseline before enabling
`vol_target` on a live bot.

**Deployment rule for anything new:** screen → backtest → walk-forward →
paper bot → only then does it earn a permanent config. The dashboard makes
the first three easier; it does not make the gate optional.

---

## 10. New bot config reference

Everything optional; omitted keys = old behavior.

```json
{
  "symbols": ["USO"],
  "strategy": "meanrev",        // meanrev | momentum | pairs | breakout | pyramid | xsec
  "interval": "5Min",
  "broker": "alpaca",
  "max_daily_loss": 3.0,         // resets DAILY now; halt clears next day
  "max_per_symbol": 0.25,
  "vol_target": 0.15,            // OPTIONAL: annualized vol to size for; omit = fixed
  "regime_filter": true,         // OPTIONAL: ER veto (trend blocks chop, meanrev blocks trends)
  "news_filter": true,
  "event_blackout": true,
  "earnings_blackout": false,    // no-op for ETFs; enable for XOM/CVX/COP
  "sec_blackout": false,
  "enabled": true
}
```
Constraints enforced everywhere (dashboard, run_bot, run_paper): pairs =
exactly 2 symbols; xsec = 3+; two bots can't share a symbol.

---

## 11. Rollback

Any phase goes wrong and you want out:
```bash
cd ~/algo-trading
```
```bash
tar -xzf ~/algo-backup-YYYYMMDD.tar.gz    # your backup from Phase 3
```
```bash
sudo systemctl restart trading-bot@uso-meanrev dashboard
```
The old sudoers file and dashboard.service are inside the backup's `deploy/`
if you need to restore those too (`sudo cp` + `daemon-reload` as above).
Configs in `bots/` were never modified by the upgrade itself.

---

## 12. Troubleshooting quick hits

| Symptom | Cause / fix |
|---|---|
| Start/Stop buttons fail after upgrade | Old sudoers still in place — redo Phase 4. Test: `sudo -n systemctl enable --now trading-bot@uso-meanrev.service` |
| Bot exits: "Catalyst filter flags were set, but…" | MERGE POINT not merged (Phase 2.3). Port your old filter block in. |
| Bot exits: `unrecognized arguments: --vol-target` | Old `run_paper.py` still on the server — re-scp it. |
| `ModuleNotFoundError: sizing` | `sizing.py` didn't land at the project root, or the bot's WorkingDirectory isn't the project root. |
| Dashboard: login loop | `DASHBOARD_SECRET_KEY` unset **and** old service file (2 sync workers) still active — redo Phase 6. |
| Research stuck "Running" | Long walk-forwards genuinely take minutes; 30-min <!-- asof:research-job-cap-minutes --> hard cap. If the dashboard service restarted mid-job, the job died — re-run. |
| Catalysts panel missing | `research/__init__.py` missing: `touch ~/algo-trading/research/__init__.py` |
| Red OPEC banner | `OPEC_DATES` exhausted — refresh from opec.org. Until then OPEC meetings are NOT covered. |
| EDGAR 403 | `SEC_USER_AGENT` not set in that environment (`alpaca.env` for the service, `~/.bashrc` for your shell). |
| Filter "never fires" | For USO: EIA is Wed ~10:30 ET (Thu on holiday weeks) + OPEC dates. OPEC+ meets on Sundays, so its veto fires at the next session's open (usually Mon 09:30-13:30 ET). Confirm wiring via the `Catalyst filters active:` startup line. |

---

*Paper trading only. Not financial advice.*
