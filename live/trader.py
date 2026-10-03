"""
Broker-agnostic live trading loop + risk manager.

This is the live counterpart to backtest/engine.py. It runs the exact same
Strategy objects, pulling real bars and sending real (paper) orders through
whatever Broker it's handed — Alpaca for stocks/ETFs/options, IBKR for
futures/crude. Nothing here knows or cares which broker it is.

CHANGES vs previous version:
  - RiskManager now truly resets DAILY. Before, start_equity was set once at
    launch, so a bot running 24/7 under systemd was actually enforcing a
    since-launch drawdown limit, not a daily one.
  - A halt now clears at the next day roll by default (resume_next_day=True);
    set it False if you want a tripped kill switch to stay tripped until you
    restart the service manually.
  - Optional volatility-targeted sizing via a shared sizer (sizing.py), so
    live sizing matches the backtest exactly.
  - Portfolio-level gross exposure cap (max_gross_leverage, default 1.0 =
    never exceed equity). This is also the future leverage switch: raising
    it above 1.0 is the one deliberate change needed to allow leverage.
"""

import pandas as pd
from strategies.base import Context
from live.broker_base import Broker

try:
    from sizing import VolatilityTargeting  # noqa: F401  (optional import)
except ImportError:
    VolatilityTargeting = None


class RiskManager:
    """The layer that keeps one bad day from ending the account. Checked
    before every order; trips a kill switch that flattens and halts.

    The daily loss limit is measured from the FIRST equity reading of each
    calendar day, and both the baseline and (optionally) the halt flag reset
    when the day rolls over."""

    def __init__(self, max_daily_loss_pct=3.0, max_gross_per_symbol=0.25,
                 max_gross_leverage=1.0, resume_next_day=True):
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_gross_per_symbol = max_gross_per_symbol
        # 1.0 = total |position notional| never exceeds equity. Leverage
        # later = raise this. Leave at 1.0 for now.
        self.max_gross_leverage = max_gross_leverage
        self.resume_next_day = resume_next_day
        self.start_equity = None
        self.halted = False
        self._day = None

    def _roll_day(self, now: pd.Timestamp | None = None):
        today = (now or pd.Timestamp.now()).date()
        if self._day != today:
            self._day = today
            self.start_equity = None          # re-anchor to today's open equity
            if self.resume_next_day and self.halted:
                print("  risk: new day — clearing halt and re-anchoring equity.")
                self.halted = False

    def register_equity(self, equity, now=None):
        self._roll_day(now)
        if self.start_equity is None:
            self.start_equity = equity

    def check(self, equity, now=None) -> bool:
        """Returns True if trading may continue."""
        self._roll_day(now)
        if self.start_equity is None:
            self.start_equity = equity
        dd = (equity / self.start_equity - 1.0) * 100
        if dd <= -self.max_daily_loss_pct:
            if not self.halted:
                print(f"  risk: daily P&L {dd:+.2f}% breached "
                      f"-{self.max_daily_loss_pct}% limit.")
            self.halted = True
        return not self.halted


class LiveTrader:
    """
    The live loop. Same shape as the backtest engine, same Strategy objects,
    but pulling real bars and sending real (paper) orders. Polls on an
    interval; for true tick-level reaction you'd swap polling for a
    websocket/streaming feed, but per-minute polling is plenty for the
    second-to-minute strategies here.

    sizer: optional object with .scale(close_series) -> float (see
    sizing.VolatilityTargeting). Applied multiplicatively to each symbol's
    weight, identically to the backtest engine.
    """
    def __init__(self, broker: Broker, strategy, symbols, timeframe="1Min",
                 lookback=120, risk: RiskManager | None = None,
                 poll_seconds=60, sizer=None):
        self.broker = broker
        self.strategy = strategy
        self.symbols = symbols
        self.timeframe = timeframe
        self.lookback = lookback
        self.risk = risk or RiskManager()
        self.poll_seconds = poll_seconds
        self.sizer = sizer

    def _build_context(self):
        acct = self.broker.get_account()
        history, positions = {}, {}
        for s in self.symbols:
            history[s] = self.broker.get_bars(s, self.lookback, self.timeframe)
            positions[s] = self.broker.get_position(s)
        return Context(history=history, positions=positions,
                       cash=acct["cash"], equity=acct["equity"]), acct

    def step(self):
        if not self.broker.is_market_open():
            print("  market closed; waiting.")
            return
        ctx, acct = self._build_context()
        self.risk.register_equity(acct["equity"])
        if not self.risk.check(acct["equity"]):
            print("  !! RISK HALT: daily loss limit hit. Flattening.")
            for s in self.symbols:
                pos = self.broker.get_position(s)
                if pos != 0:
                    self.broker.submit_order(s, -pos)
            return

        targets = self.strategy.on_bar(ctx)
        equity = acct["equity"]
        cap = self.risk.max_gross_per_symbol

        # Pass 1: compute per-symbol target dollars (with optional vol
        # scaling), so we can enforce the portfolio-level gross cap before
        # sending anything.
        plan = {}          # symbol -> (target_units, price)
        for symbol, weight in targets.items():
            h = ctx.history.get(symbol)
            if h is None or h.empty:
                continue
            price = float(h["close"].iloc[-1])
            weight = max(-1.0, min(1.0, weight))
            if self.sizer is not None and weight != 0.0:
                weight *= self.sizer.scale(h["close"])
            plan[symbol] = ((weight * equity * cap) / price, price)

        # Portfolio gross cap: planned notional + notional of held symbols
        # the strategy didn't target must fit inside equity * leverage.
        budget = equity * self.risk.max_gross_leverage
        held_other = 0.0
        for s in self.symbols:
            if s in plan:
                continue
            pos = ctx.positions.get(s, 0.0)
            h = ctx.history.get(s)
            if pos and h is not None and not h.empty:
                held_other += abs(pos) * float(h["close"].iloc[-1])
        planned_gross = sum(abs(u) * p for u, p in plan.values())
        avail = max(0.0, budget - held_other)
        if planned_gross > avail and planned_gross > 0:
            f = avail / planned_gross
            print(f"  gross cap: scaling targets by {f:.2f} to stay within "
                  f"{self.risk.max_gross_leverage:.1f}x equity.")
            plan = {s: (u * f, p) for s, (u, p) in plan.items()}

        # Pass 2: trade the deltas.
        for symbol, (target_units, price) in plan.items():
            delta = target_units - self.broker.get_position(symbol)
            if abs(delta * price) < max(1.0, 0.002 * equity):
                continue
            print(f"  {symbol}: order {delta:+.1f} units @ ~{price:.2f} "
                  f"(target {target_units:+.1f})")
            self.broker.submit_order(symbol, delta)

    def run(self):
        print(f"LIVE (paper) {self.strategy.name} on {self.symbols} "
              f"@ {self.timeframe}, every {self.poll_seconds}s. Ctrl-C to stop.")
        try:
            while True:
                stamp = pd.Timestamp.now().strftime("%H:%M:%S")
                print(f"[{stamp}] tick")
                try:
                    self.step()
                except Exception as e:
                    print(f"  error in step (continuing): {e}")
                self.broker.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            self.broker.disconnect()
