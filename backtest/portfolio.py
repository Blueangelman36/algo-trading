"""
Portfolio: the single source of truth for cash, positions, and P&L.

Critically, this is where commission and slippage live. The most common
way a backtest lies is by ignoring these — a strategy that looks great at
zero cost often bleeds out once you charge realistic fees and assume you
get filled slightly worse than the price you saw. We model both.
"""

from dataclasses import dataclass, field
import pandas as pd


@dataclass
class Fill:
    timestamp: pd.Timestamp
    symbol: str
    units: float        # +ve buy, -ve sell
    price: float        # fill price AFTER slippage
    commission: float


@dataclass
class Portfolio:
    starting_cash: float = 100_000.0
    commission_per_share: float = 0.005   # Alpaca-style; set 0 for commission-free
    commission_min: float = 1.0           # min ticket charge
    slippage_bps: float = 1.0             # 1 bp = 0.01% adverse fill

    cash: float = field(init=False)
    positions: dict[str, float] = field(default_factory=dict)   # symbol -> units
    fills: list[Fill] = field(default_factory=list)
    equity_curve: list[tuple] = field(default_factory=list)     # (timestamp, equity)

    def __post_init__(self):
        self.cash = self.starting_cash

    def units(self, symbol: str) -> float:
        return self.positions.get(symbol, 0.0)

    def market_value(self, prices: dict[str, float]) -> float:
        return sum(u * prices.get(s, 0.0) for s, u in self.positions.items())

    def gross_exposure(self, prices: dict[str, float]) -> float:
        """Sum of |units| * price across positions — the number the engine's
        max_gross_leverage cap is enforced against."""
        return sum(abs(u) * prices.get(s, 0.0) for s, u in self.positions.items())

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.market_value(prices)

    def execute(self, timestamp, symbol: str, units: float, ref_price: float):
        """Execute a trade of `units` (signed) at a price derived from ref_price
        plus adverse slippage. Updates cash and positions."""
        if units == 0:
            return
        slip = self.slippage_bps / 10_000.0
        # buying pushes price up against you, selling pushes it down
        fill_price = ref_price * (1 + slip) if units > 0 else ref_price * (1 - slip)
        commission = max(self.commission_min, abs(units) * self.commission_per_share)

        self.cash -= units * fill_price
        self.cash -= commission
        self.positions[symbol] = self.units(symbol) + units
        if abs(self.positions[symbol]) < 1e-9:
            self.positions.pop(symbol, None)

        self.fills.append(Fill(timestamp, symbol, units, fill_price, commission))

    def mark(self, timestamp, prices: dict[str, float]):
        self.equity_curve.append((timestamp, self.equity(prices)))

    def equity_series(self) -> pd.Series:
        if not self.equity_curve:
            return pd.Series(dtype=float)
        ts, eq = zip(*self.equity_curve)
        return pd.Series(eq, index=pd.DatetimeIndex(ts), name="equity")
