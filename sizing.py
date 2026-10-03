"""
Volatility-targeted position sizing.

Fixed-fractional sizing ("always risk w * equity") quietly means your RISK
swings with the market: the same dollar position in USO is a very different
bet at 15% annualized vol than at 60%. Volatility targeting fixes the risk
instead of the dollars — when realized vol rises, positions shrink; when it
falls, they grow (up to a cap). The effect across strategy types is smoother
equity curves and shallower drawdowns for the same signals.

This module is shared by the backtest engine and the live trader so both
size positions identically. Usage:

    from sizing import VolatilityTargeting, periods_per_year

    sizer = VolatilityTargeting(target_annual_vol=0.15,
                                periods_per_year=periods_per_year("5Min"))
    scale = sizer.scale(close_series)   # multiply your weight by this

LEVERAGE NOTE: with max_scale > 1.0 a calm market can push target exposure
above your base weight. The engine/trader still cap total exposure with
max_gross_leverage (default 1.0 = no leverage). Raising max_gross_leverage
is the single switch to flip when/if leverage is ever wanted — leave it at
1.0 for now.
"""

import math
import pandas as pd


# Bars per year for the timeframes used across the project. Equity session
# assumed ~6.5h; futures trade longer, but consistency matters more than
# precision here (the sizer is a ratio, so a uniform bias mostly cancels).
_PPY = {
    "1Min": 252 * 6.5 * 60,
    "5Min": 252 * 6.5 * 12,
    "15Min": 252 * 6.5 * 4,
    "1Hour": 252 * 6.5,
    "1Day": 252.0,
}


def periods_per_year(timeframe: str) -> float:
    return _PPY.get(timeframe, 252.0)


class VolatilityTargeting:
    """Scale factor = target_vol / realized_vol, clamped to [min_scale,
    max_scale]. Returns 1.0 whenever there isn't enough history to estimate
    vol — i.e. it degrades to your current behavior, never blows up."""

    def __init__(self, target_annual_vol: float = 0.15, lookback: int = 20,
                 periods_per_year: float = 252.0,
                 min_scale: float = 0.1, max_scale: float = 1.0):
        self.target = target_annual_vol
        self.lookback = lookback
        self.ppy = periods_per_year
        self.min_scale = min_scale
        # max_scale defaults to 1.0 so vol targeting can only DE-risk, never
        # lever up. Raise it above 1.0 only together with a conscious
        # max_gross_leverage decision.
        self.max_scale = max_scale

    def realized_vol(self, closes: pd.Series) -> float | None:
        rets = closes.pct_change().dropna()
        if len(rets) < max(5, self.lookback // 2):
            return None
        window = rets.iloc[-self.lookback:]
        sd = float(window.std())
        if sd <= 0 or math.isnan(sd):
            return None
        return sd * math.sqrt(self.ppy)

    def scale(self, closes: pd.Series) -> float:
        rv = self.realized_vol(closes)
        if rv is None:
            return 1.0
        return max(self.min_scale, min(self.max_scale, self.target / rv))
