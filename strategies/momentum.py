"""
Momentum / trend following via dual moving-average crossover with an
optional breakout filter.

Idea: ride trends. Go long when the fast MA is above the slow MA (and,
if enabled, price makes a new N-bar high); flip/flatten when it isn't.
The mirror image of mean reversion: this makes money in sustained trends
(think a directional move in crude) and chops up in range-bound markets.

Params:
  fast, slow: moving-average windows
  breakout:   if >0, also require a new `breakout`-bar high to go long
  allow_short: if True, go short on the bearish cross instead of just flat
"""

from strategies.base import Strategy, Context


class Momentum(Strategy):
    def __init__(self, symbols, fast=20, slow=50, breakout=0, allow_short=True):
        super().__init__(symbols, fast=fast, slow=slow,
                         breakout=breakout, allow_short=allow_short)
        self.fast = fast
        self.slow = slow
        self.breakout = breakout
        self.allow_short = allow_short

    def on_bar(self, ctx: Context) -> dict[str, float]:
        targets = {}
        need = max(self.slow, self.breakout) + 1
        for sym in self.symbols:
            h = ctx.hist(sym)
            if len(h) < need:
                continue
            closes = h["close"]
            fast_ma = closes.rolling(self.fast).mean().iloc[-1]
            slow_ma = closes.rolling(self.slow).mean().iloc[-1]
            price = closes.iloc[-1]

            bullish = fast_ma > slow_ma
            if self.breakout > 0:
                hi = h["high"].rolling(self.breakout).max().iloc[-2]  # prior window
                bullish = bullish and price >= hi

            if bullish:
                targets[sym] = 1.0
            else:
                targets[sym] = -1.0 if self.allow_short else 0.0
        return targets
