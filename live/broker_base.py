"""
Broker abstraction. The live loop talks to this interface, never to a
specific vendor. Alpaca is the first concrete implementation (stocks +
options, free paper trading). To add futures/oil later you write an
InteractiveBrokersBroker with the same methods and nothing else changes.
"""

from abc import ABC, abstractmethod
import pandas as pd


class Broker(ABC):
    @abstractmethod
    def get_account(self) -> dict:
        """Return at least {'equity': float, 'cash': float, 'buying_power': float}."""

    @abstractmethod
    def get_position(self, symbol: str) -> float:
        """Signed units currently held (0 if flat)."""

    @abstractmethod
    def get_bars(self, symbol: str, lookback: int, timeframe: str) -> pd.DataFrame:
        """Recent OHLCV bars indexed by timestamp (lowercased columns)."""

    @abstractmethod
    def submit_order(self, symbol: str, units: float):
        """Market order. +units buy, -units sell. Implementation rounds to
        whole shares/contracts as the venue requires."""

    @abstractmethod
    def is_market_open(self) -> bool:
        ...

    def sleep(self, seconds: float):
        """Wait between polling cycles. Default is a plain sleep; brokers
        that run their own event loop (e.g. IBKR/ib_async) override this to
        pump the loop while waiting, so background messages keep flowing."""
        import time
        time.sleep(seconds)

    def disconnect(self):
        """Optional cleanup hook; brokers with a persistent socket override."""
        pass
