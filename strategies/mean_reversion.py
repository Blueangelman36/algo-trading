"""
Mean reversion via z-score (Bollinger-style).

Idea: when price stretches far above its recent average, fade it (short);
when far below, buy the dip. Works in range-bound, choppy markets — which
oil and many commodities often are — and bleeds during strong trends.

Params:
  lookback: window for the moving average / std
  entry_z:  enter when |z| exceeds this
  exit_z:   exit back toward flat when |z| falls below this
"""

from strategies.base import Strategy, Context


class MeanReversion(Strategy):
    def __init__(self, symbols, lookback=20, entry_z=2.0, exit_z=0.5):
        super().__init__(symbols, lookback=lookback, entry_z=entry_z, exit_z=exit_z)
        self.lookback = lookback
        self.entry_z = entry_z
        self.exit_z = exit_z

    def on_bar(self, ctx: Context) -> dict[str, float]:
        targets = {}
        for sym in self.symbols:
            h = ctx.hist(sym)
            if len(h) < self.lookback + 1:
                continue
            closes = h["close"]
            ma = closes.rolling(self.lookback).mean().iloc[-1]
            sd = closes.rolling(self.lookback).std().iloc[-1]
            if sd is None or sd == 0 or sd != sd:  # nan guard
                continue
            z = (closes.iloc[-1] - ma) / sd
            current = ctx.positions.get(sym, 0.0)

            if z >= self.entry_z:
                targets[sym] = -1.0            # too high -> short
            elif z <= -self.entry_z:
                targets[sym] = 1.0             # too low -> long
            elif abs(z) <= self.exit_z and current != 0:
                targets[sym] = 0.0            # reverted -> close
            # else: hold whatever we have (emit nothing)
        return targets
