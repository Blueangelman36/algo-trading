"""
Alpaca paper-trading broker adapter (stocks / ETFs / options).

Uses Alpaca's PAPER endpoint — fake money, real market data, real order
mechanics. The live loop itself lives in live/trader.py; this file only
implements the Broker interface for Alpaca.

Setup:
  pip install alpaca-py
  Free account at alpaca.markets -> generate PAPER api keys -> set env vars
  (never hardcode keys):
      ALPACA_API_KEY
      ALPACA_SECRET_KEY

Asset classes:
  - Stocks & ETFs (incl. oil ETFs like USO): supported here.
  - Options: Alpaca supports them; option symbols + sizing differ, so that's
    a focused follow-on. This adapter handles equities.
  - Futures / commodity futures (crude CL): NOT on Alpaca -> use IBKR
    (live/ibkr_bot.py).

CHANGE vs previous version: get_bars sized its history request in raw
minutes (lookback * 3 + 60), which worked for minute bars but silently broke
for 1Hour/1Day — asking for 120 daily bars fetched only a few hours of
history and returned one bar. The window now scales with the timeframe.
"""

import os
import pandas as pd
from live.broker_base import Broker


class AlpacaBroker(Broker):
    # minutes per bar for each supported timeframe, used to size the
    # history request. The 3x multiplier + 60min pad covers weekends,
    # holidays, and closed-session gaps.
    _BAR_MINUTES = {"1Min": 1, "5Min": 5, "15Min": 15,
                    "1Hour": 60, "1Day": 1440}

    def __init__(self, paper=True):
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient
        key = os.environ.get("ALPACA_API_KEY")
        secret = os.environ.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise SystemExit("Set ALPACA_API_KEY and ALPACA_SECRET_KEY env vars.")
        self.trading = TradingClient(key, secret, paper=paper)
        self.data = StockHistoricalDataClient(key, secret)

    def get_account(self) -> dict:
        a = self.trading.get_account()
        return {"equity": float(a.equity), "cash": float(a.cash),
                "buying_power": float(a.buying_power)}

    def get_position(self, symbol: str) -> float:
        try:
            pos = self.trading.get_open_position(symbol)
            return float(pos.qty)
        except Exception:
            return 0.0

    def get_bars(self, symbol: str, lookback: int, timeframe: str) -> pd.DataFrame:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        tf_map = {
            "1Min": TimeFrame(1, TimeFrameUnit.Minute),
            "5Min": TimeFrame(5, TimeFrameUnit.Minute),
            "15Min": TimeFrame(15, TimeFrameUnit.Minute),
            "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
            "1Day": TimeFrame(1, TimeFrameUnit.Day),
        }
        tf = tf_map.get(timeframe, TimeFrame(1, TimeFrameUnit.Minute))
        per_bar = self._BAR_MINUTES.get(timeframe, 1)
        start = pd.Timestamp.utcnow() - pd.Timedelta(
            minutes=lookback * per_bar * 3 + 60)
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=tf, start=start)
        bars = self.data.get_stock_bars(req).df
        if bars.empty:
            return pd.DataFrame()
        if isinstance(bars.index, pd.MultiIndex):
            bars = bars.xs(symbol, level="symbol")
        bars = bars.rename(columns=str.lower)
        return bars[["open", "high", "low", "close", "volume"]].tail(lookback)

    def submit_order(self, symbol: str, units: float):
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        units = int(round(units))
        if units == 0:
            return None
        side = OrderSide.BUY if units > 0 else OrderSide.SELL
        order = MarketOrderRequest(symbol=symbol, qty=abs(units), side=side,
                                   time_in_force=TimeInForce.DAY)
        return self.trading.submit_order(order)

    def is_market_open(self) -> bool:
        return bool(self.trading.get_clock().is_open)
