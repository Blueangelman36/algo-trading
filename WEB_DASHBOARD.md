# Web Dashboard — Setup & Upgrade Guide (v2)

A browser UI for your server: run the screener/backtest/walk-forward from a form,
manage bots (create, edit, start/stop/restart), watch live status and logs, and
see upcoming market catalysts — no SSH, no editing systemd files by hand.

Commands below assume the server layout from `CLOUD_DEPLOYMENT.md`: user
`trader`, project at `/home/trader/algo-trading`. Replace `YOUR_IP` with your
server's address, and `trader` with your user if you chose a different one.

---

## What's new in v2

**Fixes**
- **Research no longer freezes the dashboard.** Jobs run in the background; the
  page polls for results. You can start a 15-minute walk-forward, browse away,
  and come back — and the Stop buttons on your bots stay responsive throughout.
- **No more random logouts.** Sessions are now stable across gunicorn workers.
- **Start/Stop are persistent.** Start = `systemctl enable --now` (survives
  reboot); Stop = `disable --now` (stays stopped after reboot). Deleting a bot
  disables its unit first — previously a deleted bot's enabled unit went into a
  restart loop after the next reboot.
- **Bots table columns fixed** (Interval was missing, shifting everything over).
- Security hardening: CSRF tokens on every action, login rate-limiting,
  open-redirect fix, pinned sudo journalctl arguments.

**New features**
- **Live status** — bot pills refresh every 8 seconds <!-- asof:status-refresh-seconds --> without reloading.
- **Edit button** — loads a bot's config into the form instead of retyping it.
- **New strategies** — breakout (Donchian), pyramiding breakout, cross-sectional
  momentum, alongside meanrev/momentum/pairs.
- **Vol targeting & regime filter** — per-bot fields in the form (see below).
- **Upcoming catalysts panel** — the next 7 days of EIA/OPEC/rig-count events on
  the Bots page, from the same calendar the event-blackout filter uses. Warns
  loudly when the hardcoded OPEC dates have gone stale.
- **Logs auto-refresh** every 5 seconds <!-- asof:log-refresh-seconds -->, with VETO and RISK HALT lines
  highlighted.

---

## Prerequisites

- The catalyst filters installed (`research/`, `data/news.py`,
  `strategies/news_filter.py`).
- The updated framework files deployed (`live/trader.py` with the `sizer`
  parameter, `sizing.py` at the project root, the new `strategies/*.py`).
- `run_paper.py` v2 with your existing catalyst-filter wiring merged into its
  marked MERGE POINT block. Verify:
  ```bash
  grep -c "event-blackout" ~/algo-trading/run_paper.py    # expect 2 or more
  grep -c "vol-target" ~/algo-trading/run_paper.py        # expect 1 or more
  ```

## How bots run (unchanged design)

```
bots/uso-meanrev.json     <- the dashboard edits these (no root needed)
run_bot.py                <- reads the JSON, launches run_paper.py with it
trading-bot@.service      <- one unit file that can run ANY bot by name
```

The dashboard only ever writes JSON files it owns. The only privileged thing it
can do is `systemctl` on units named `trading-bot@*`, granted by a narrow
sudoers rule.

---

## Upgrading from v1 — do these IN ORDER

The sudoers update must land **before** you touch the new Start/Stop buttons,
or they'll fail with a sudo password prompt error.

**1. Copy the new files up** *(desktop)*:
```powershell
scp -r webapp deploy run_paper.py run_bot.py sizing.py trader@YOUR_IP:/home/trader/algo-trading/
scp -r strategies live backtest trader@YOUR_IP:/home/trader/algo-trading/
```

**2. Merge your catalyst-filter wiring** *(server)* into `run_paper.py`'s
MERGE POINT block, then verify everything imports:
```bash
cd ~/algo-trading && source venv/bin/activate
python -m py_compile run_paper.py run_bot.py webapp/app.py
```

**3. Update the sudoers rule** *(server)*:
```bash
sudo cp ~/algo-trading/deploy/sudoers-trading /etc/sudoers.d/trading
sudo chmod 0440 /etc/sudoers.d/trading
sudo visudo -c        # must print "parsed OK" — if not, sudo rm /etc/sudoers.d/trading IMMEDIATELY
```

**4. Update the dashboard service** *(server)*:
```bash
sudo cp ~/algo-trading/deploy/dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart dashboard
sudo systemctl status dashboard    # active (running)
```

**5. Verify through the tunnel** *(desktop)*:
```powershell
ssh -L 8000:127.0.0.1:8000 trader@YOUR_IP
```
Browse to http://127.0.0.1:8000 <!-- asof:dashboard-port --> and check: status pills update on their own;
the catalysts panel shows this week's EIA report; a screener run shows a
spinner and finishes without the page hanging; Stop → Start on a bot flips
`systemctl is-enabled trading-bot@uso-meanrev` between disabled and enabled.

Fresh installs: follow v1's Parts 2–7 (env file, password, tunnel/Caddy) —
those are unchanged — but use the v2 `deploy/` files.

---

## Bot configuration reference

| Field | Meaning |
|---|---|
| Strategy | `meanrev`, `momentum`, `pairs` (exactly 2 symbols), `breakout`, `pyramid`, `xsec` (3+ symbols) |
| Max daily loss % | Risk kill switch. Resets at the day roll; halted bots resume next day. |
| Max per symbol | Fraction of equity per name (0.25 = 25%). |
| Vol target | Optional. Annualized vol to size for, e.g. `0.15`. Positions shrink when realized vol spikes. Blank = fixed-fraction sizing. |
| Regime filter | Efficiency-ratio veto: trend strategies won't enter chop, mean-rev won't enter strong trends. Never blocks exits. |
| Catalyst filters | EIA/OPEC events, news intensity/keywords, earnings, SEC 8-Ks. Defensive vetoes only. |

Guidance the form shows per strategy is a reminder, not a substitute for the
workflow: **screen → backtest → walk-forward → deploy only what survives.**
The dashboard makes the first three less tedious; it does not make the
walk-forward gate optional.

For USO specifically: `event_blackout` on (EIA/OPEC is the one that matters for
oil), `earnings_blackout` off (ETFs have no earnings — it's a no-op). OPEC+
meets on Sundays, when the market is closed, so its blackout covers the first
hours of the next session instead (usually Monday 09:30-13:30 ET).

---

## Security notes

- Password-gated (no default), rate-limited login, CSRF on every action,
  session cookies HttpOnly + SameSite=Lax.
- Every input whitelisted before it can become a command argument; subprocesses
  are argument lists, never a shell.
- The app never writes to `/etc`. Sudo is limited to
  `systemctl start/stop/restart/enable --now/disable --now` on `trading-bot@*`
  units and one pinned journalctl command.
- Reach it via SSH tunnel (recommended) or HTTPS via Caddy. **Never plain HTTP
  over the internet.**
- These are **paper** keys and this is a **paper** dashboard. If you ever point
  this at live keys, revisit every assumption first.

---

## Troubleshooting

**Start/Stop buttons fail after upgrading** — the old sudoers file is still in
place. It lacks the `enable --now` / `disable --now` lines. Redo step 3.

**Dashboard won't start** — `journalctl -u dashboard -n 40`. Usually a missing
`DASHBOARD_PASSWORD` in `dashboard.env`, or gunicorn missing from the venv.

**Research job stuck "Running"** — the job is genuinely running (walk-forwards
on long histories take minutes). Hard cap is 30 minutes <!-- asof:research-job-cap-minutes -->. If the dashboard
service restarts mid-job, the job dies with it — re-run it.

**Catalysts panel missing** — `research/event_calendar.py` isn't on the server
or `research/__init__.py` is missing (`touch ~/algo-trading/research/__init__.py`).
The panel hides itself rather than breaking the page.

**Catalysts panel shows a red OPEC warning** — the hardcoded OPEC meeting dates
have run out. Refresh `OPEC_DATES` in `research/event_calendar.py` from opec.org.
Until you do, OPEC meetings are NOT covered by the blackout filter.

**Bot won't start after editing config** — `journalctl -u trading-bot@<name> -n 30`.
`run_bot.py` validates the JSON and refuses to launch on a bad strategy name,
malformed symbol, a pairs bot without exactly 2 symbols, or an xsec bot with
fewer than 3.

*Paper trading only. Not financial advice.*
