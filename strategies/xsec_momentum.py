"""
Cross-sectional momentum: rank a UNIVERSE by trailing return, hold the
recent winners (and optionally short the losers).

This is a different animal from the time-series Momentum strategy: that one
asks "is this symbol trending vs its own past?", this asks "which symbols
are strongest RELATIVE to each other?". Documented across asset classes and
decades, typically formed on 3-12 month returns while SKIPPING the most
recent few bars (short-term reversal works against you at the very front).

Practical notes:
  - Needs a universe (5+ symbols) to mean anything. On 2 symbols it
    degenerates into a noisy pairs bet.
  - Rebalances on a schedule (default ~monthly on daily bars), not every
    bar — turnover is the tax on momentum, so don't churn.
  - Weights: selected names get +/-1.0 and everything else 0.0; the engine's
    max_gross_per_symbol scales that into dollars. With n_long=2 on a
    5-symbol universe and max_gross_per_symbol=1/5, each winner gets 20% of
    equity — set max_gross_per_symbol to taste.

Duck-typed to the project's Strategy interface: exposes .name, .params and
.on_bar(ctx) -> {symbol: weight}, so it plugs into the backtest engine,
walk-forward harness, and live trader unchanged.
"""


class CrossSectionalMomentum:
    def __init__(self, symbols, lookback=126, skip=5,
                 n_long=2, n_short=0, rebalance=21):
        """
        lookback : formation window in bars (126 daily bars ~ 6 months).
        skip     : most recent bars to EXCLUDE from the formation return
                   (classic construction; avoids short-term reversal).
        n_long   : how many top names to hold long.
        n_short  : how many bottom names to short (0 = long-only).
        rebalance: re-rank every this many bars; hold weights in between.
        """
        self.symbols = list(symbols)
        # Constructor args, as Strategy.__init__ records them; the backtest
        # and walk-forward tools size their warmup from this.
        self.params = dict(lookback=lookback, skip=skip, n_long=n_long,
                           n_short=n_short, rebalance=rebalance)
        self.lookback = lookback
        self.skip = skip
        self.n_long = n_long
        self.n_short = n_short
        self.rebalance = max(1, rebalance)
        self.name = (f"XSecMomentum(lb={lookback},skip={skip},"
                     f"L{n_long}/S{n_short},rb={rebalance})")
        self._bar = 0
        self._weights: dict = {s: 0.0 for s in self.symbols}

    def _formation_return(self, closes):
        """Return over [t-skip-lookback, t-skip]."""
        need = self.lookback + self.skip + 1
        if len(closes) < need:
            return None
        end = closes.iloc[-1 - self.skip]
        start = closes.iloc[-1 - self.skip - self.lookback]
        if start <= 0:
            return None
        return float(end / start - 1.0)

    def on_bar(self, ctx) -> dict:
        self._bar += 1
        if (self._bar - 1) % self.rebalance != 0:
            return dict(self._weights)

        scores = {}
        for s in self.symbols:
            h = ctx.history.get(s)
            if h is None or h.empty:
                continue
            r = self._formation_return(h["close"])
            if r is not None:
                scores[s] = r

        # Not enough ranked names yet (warmup): hold whatever we have.
        if len(scores) < max(2, self.n_long + self.n_short):
            return dict(self._weights)

        ranked = sorted(scores, key=scores.get, reverse=True)
        weights = {s: 0.0 for s in self.symbols}
        for s in ranked[:self.n_long]:
            weights[s] = 1.0
        if self.n_short > 0:
            for s in ranked[-self.n_short:]:
                weights[s] = -1.0
        self._weights = weights
        return dict(weights)
