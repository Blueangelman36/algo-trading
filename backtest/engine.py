"""
Event-driven backtest engine.

Walks bars forward one timestamp at a time. At each step it shows the
strategy only data up to *now*, gets target weights back, and converts
those into orders sized against current equity. This bar-by-bar loop is
deliberately the same shape as the live trading loop, so a strategy that
works here drops straight into paper trading.

Target-weight sizing: if a strategy wants weight w for a symbol, the
target dollar exposure is w * equity * max_gross_per_symbol. We compute
the unit delta from the current holding and trade only the difference.

CHANGES vs previous version:
  - fill_mode: "close" fills at the signal bar's close (old behavior —
    optimistic, because live you can't act on a close until it's printed);
    "next_open" defers execution to the NEXT bar's open, which is what the
    live loop actually experiences. If a strategy's edge survives
    next_open, it's much more likely real. Run both and compare.
  - max_gross_leverage (default 1.0): portfolio-level cap on total
    |notional| / equity. The old engine allowed cash to drift negative
    with multi-symbol shorts. 1.0 = never exceed equity; this is also the
    single knob to raise later if leverage is ever wanted.
  - sizer: optional volatility-targeting sizer (sizing.py) applied to each
    symbol's weight, identical to the live trader.
"""

import pandas as pd
from strategies.base import Context
from backtest.portfolio import Portfolio


class BacktestEngine:
    def __init__(self, strategy, data: dict[str, pd.DataFrame],
                 portfolio: Portfolio | None = None,
                 max_gross_per_symbol: float = 1.0,
                 warmup: int = 50,
                 fill_mode: str = "close",
                 max_gross_leverage: float = 1.0,
                 sizer=None):
        """
        data: symbol -> OHLCV DataFrame indexed by timestamp (columns:
              open, high, low, close, volume). All symbols are aligned
              on a shared, sorted timestamp index.
        max_gross_per_symbol: cap on |weight|*equity allocated to one name.
              With N symbols you'd typically set this to 1/N to stay within
              equity; left at 1.0 for single-symbol tests.
        warmup: bars to skip before trading, so indicators have history.
        fill_mode: "close" or "next_open" (see module docstring).
        max_gross_leverage: total |notional| may not exceed this multiple of
              equity. Leave at 1.0 (no leverage) for now.
        sizer: optional object with .scale(close_series) -> float.
        """
        if fill_mode not in ("close", "next_open"):
            raise ValueError("fill_mode must be 'close' or 'next_open'")
        self.strategy = strategy
        self.data = data
        self.portfolio = portfolio or Portfolio()
        self.max_gross = max_gross_per_symbol
        self.warmup = warmup
        self.fill_mode = fill_mode
        self.max_lev = max_gross_leverage
        self.sizer = sizer

        # Build the master timeline = union of all symbols' timestamps.
        idx = None
        for df in data.values():
            idx = df.index if idx is None else idx.union(df.index)
        self.timeline = idx.sort_values()

    def _price_at(self, symbol, ts, col="close"):
        df = self.data[symbol]
        if ts in df.index:
            return float(df.at[ts, col])
        return None

    # ---- order construction & caps --------------------------------------
    def _execute_targets(self, ts, targets: dict, prices: dict, equity: float):
        """Convert target weights into capped, gross-limited orders and
        execute them against the portfolio at `prices`."""
        plan = {}   # symbol -> target_units
        for symbol, weight in targets.items():
            price = prices.get(symbol)
            if price is None or price <= 0:
                continue
            weight = max(-1.0, min(1.0, weight))
            if self.sizer is not None and weight != 0.0:
                closes = self.data[symbol]["close"].loc[:ts]
                weight *= self.sizer.scale(closes)
            plan[symbol] = (weight * equity * self.max_gross) / price

        # Portfolio-level gross cap: planned notional plus notional of held
        # symbols the strategy didn't target must fit within equity * lev.
        budget = equity * self.max_lev
        held_other = sum(abs(u) * prices.get(s, 0.0)
                         for s, u in self.portfolio.positions.items()
                         if s not in plan)
        planned_gross = sum(abs(u) * prices.get(s, 0.0)
                            for s, u in plan.items())
        avail = max(0.0, budget - held_other)
        if planned_gross > avail and planned_gross > 0:
            f = avail / planned_gross
            plan = {s: u * f for s, u in plan.items()}

        for symbol, target_units in plan.items():
            price = prices[symbol]
            delta = target_units - self.portfolio.units(symbol)
            # Skip dust trades to avoid churning on commission.
            if abs(delta * price) < max(1.0, 0.001 * equity):
                continue
            self.portfolio.execute(ts, symbol, delta, price)

    # ---- main loop -------------------------------------------------------
    def run(self) -> Portfolio:
        pending = None   # targets computed last bar, awaiting next-open fill
        for i, ts in enumerate(self.timeline):
            closes = {s: self._price_at(s, ts, "close") for s in self.data}
            closes = {s: p for s, p in closes.items() if p is not None}

            # Fill last bar's targets at THIS bar's open.
            if self.fill_mode == "next_open" and pending is not None:
                opens = {s: self._price_at(s, ts, "open") for s in self.data}
                opens = {s: p for s, p in opens.items() if p is not None}
                if opens:
                    equity_open = self.portfolio.equity(opens)
                    self._execute_targets(ts, pending, opens, equity_open)
                pending = None

            if i >= self.warmup:
                # Build the context: history up to and including this bar.
                ctx = Context(
                    history={s: df.loc[:ts] for s, df in self.data.items()},
                    positions=dict(self.portfolio.positions),
                    cash=self.portfolio.cash,
                    equity=self.portfolio.equity(closes),
                )
                targets = self.strategy.on_bar(ctx)
                if self.fill_mode == "close":
                    self._execute_targets(ts, targets, closes, ctx.equity)
                else:
                    pending = targets

            # Mark to market AFTER trading, so the equity curve at each bar's
            # close reflects that bar's trades and their costs. (Marking
            # before trading silently omits the final bar's commission/slippage.)
            self.portfolio.mark(ts, closes)

        return self.portfolio
