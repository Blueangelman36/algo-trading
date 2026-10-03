"""
Strategy interface.

The whole point of this design: a strategy emits *target positions*
(desired exposure per symbol), not raw orders. The engine — whether
backtest or live — translates targets into actual orders given the
current portfolio. That means the identical strategy object you backtest
is the one you paper-trade. No rewriting logic between the two.

A strategy sees one bar at a time plus a rolling history window, so it
can never accidentally peek at future data (the #1 way backtests lie).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import pandas as pd


@dataclass
class Bar:
    """One OHLCV bar for one symbol at one timestamp."""
    timestamp: pd.Timestamp
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Context:
    """
    What a strategy can see when deciding. History is a DataFrame of all
    bars up to and including the current one (indexed by timestamp), so a
    strategy can compute moving averages, z-scores, etc. without lookahead.
    """
    history: dict[str, pd.DataFrame] = field(default_factory=dict)  # symbol -> OHLCV df
    positions: dict[str, float] = field(default_factory=dict)       # symbol -> current units held
    cash: float = 0.0
    equity: float = 0.0

    def hist(self, symbol: str) -> pd.DataFrame:
        return self.history.get(symbol, pd.DataFrame())


class Strategy(ABC):
    """
    Subclass this and implement on_bar. Return a dict mapping symbol ->
    target weight in [-1, 1], where:
        1.0  = full long  (use ~100% of allocated equity)
        0.0  = flat
       -1.0  = full short
    Symbols you don't mention are left unchanged. Return {} to do nothing.
    """

    def __init__(self, symbols: list[str], **params):
        self.symbols = symbols
        self.params = params

    @abstractmethod
    def on_bar(self, ctx: Context) -> dict[str, float]:
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__
