"""
Pairs trading (statistical arbitrage).

The most robust of the three strategies because it's market-neutral: you're
not betting on where the market goes, only that two historically-linked
instruments that have drifted apart will converge again. Classic energy
examples: two crude ETFs (USO / BNO), or crude vs an oil-producer basket.

Mechanics (dollar-neutral, log-spread version):
  spread = log(priceA) - log(priceB)
  z = (spread_now - mean(spread)) / std(spread)   over `lookback`
  - z >= entry_z : A is rich relative to B -> SHORT A, LONG B
  - z <= -entry_z: A is cheap relative to B -> LONG A, SHORT B
  - |z| <= exit_z: spread has reverted -> close both legs
Each leg gets equal dollar weight (+1 / -1), so the position is roughly
market-neutral. A refinement is an OLS hedge ratio (beta) instead of 1:1;
that's noted in the code below but off by default for clarity.

IMPORTANT: this only makes money if the two instruments are actually
cointegrated. Run an Engle-Granger or ADF test on the spread before trusting
it (statsmodels.tsa.stattools.coint) — a pair that *looks* correlated but
isn't cointegrated will happily diverge forever and blow through your stop.
"""

import numpy as np
from strategies.base import Strategy, Context


class PairsTrading(Strategy):
    def __init__(self, symbols, lookback=60, entry_z=2.0, exit_z=0.5,
                 use_hedge_ratio=False):
        if len(symbols) != 2:
            raise ValueError("PairsTrading needs exactly 2 symbols, "
                             f"got {symbols}")
        super().__init__(symbols, lookback=lookback, entry_z=entry_z,
                         exit_z=exit_z, use_hedge_ratio=use_hedge_ratio)
        self.a, self.b = symbols
        self.lookback = lookback
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.use_hedge_ratio = use_hedge_ratio

    def on_bar(self, ctx: Context) -> dict[str, float]:
        ha, hb = ctx.hist(self.a), ctx.hist(self.b)
        if len(ha) < self.lookback + 1 or len(hb) < self.lookback + 1:
            return {}

        # Align both legs on their shared timestamps, then take the window.
        pa = ha["close"]
        pb = hb["close"]
        common = pa.index.intersection(pb.index)
        if len(common) < self.lookback + 1:
            return {}
        pa = pa.loc[common].iloc[-self.lookback:]
        pb = pb.loc[common].iloc[-self.lookback:]
        if (pa <= 0).any() or (pb <= 0).any():
            return {}

        la, lb = np.log(pa.values), np.log(pb.values)

        if self.use_hedge_ratio:
            # OLS slope of la on lb over the window; spread = la - beta*lb.
            beta = np.cov(la, lb, ddof=0)[0, 1] / np.var(lb)
        else:
            beta = 1.0

        spread = la - beta * lb
        mu, sd = spread.mean(), spread.std()
        if sd == 0 or np.isnan(sd):
            return {}
        z = (spread[-1] - mu) / sd

        holding = ctx.positions.get(self.a, 0.0) != 0 or \
                  ctx.positions.get(self.b, 0.0) != 0

        if z >= self.entry_z:
            return {self.a: -1.0, self.b: 1.0}     # A rich -> short A / long B
        if z <= -self.entry_z:
            return {self.a: 1.0, self.b: -1.0}     # A cheap -> long A / short B
        if abs(z) <= self.exit_z and holding:
            return {self.a: 0.0, self.b: 0.0}      # reverted -> flat both
        return {}                                   # hold
