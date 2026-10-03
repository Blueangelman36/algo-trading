"""
Alpaca options layer (paper) — defined-risk long options.

Scope on purpose: this BUYS calls and puts (long premium). Your max loss is
the premium you pay, full stop — no naked short options, no assignment risk,
no margin blowups. That's the right place to start automating options.
Spreads and short premium are a deliberate next step, not a first one.

What this handles:
  - discover the option chain for an underlying (by expiry window + type)
  - select a contract by moneyness (ATM/OTM by strike) or by target delta
  - quote a contract (bid/ask/mid)
  - buy-to-open and sell-to-close single-leg positions
  - list current option positions

Requires: pip install alpaca-py, and paper keys in ALPACA_API_KEY /
ALPACA_SECRET_KEY. Options must be enabled on the (free) paper account.

RISK NOTE: options decay (theta) and can expire worthless even if you're
right on direction but wrong on timing. Delta-based selection needs the
options Greeks feed; without an OPRA subscription Alpaca returns *indicative*
greeks, so strike-based (ATM) selection is the robust default here.
This is not financial advice.
"""

import os
from datetime import date, datetime, timedelta
import pandas as pd


# ---- pure selection logic (no API; unit-testable) ----------------------

def pick_expiry(expiries: list, target_dte: int, today: date) -> date:
    """Choose the expiration whose days-to-expiry is closest to target_dte."""
    if not expiries:
        raise ValueError("no expiries to choose from")
    return min(expiries, key=lambda e: abs((e - today).days - target_dte))


def pick_atm_strike(strikes: list, spot: float, otm_offset: float = 0.0,
                    option_type: str = "call") -> float:
    """Pick the strike nearest to spot, optionally shifted out-of-the-money.
    otm_offset is a fraction (0.03 = ~3% OTM). For a call, OTM is above spot;
    for a put, below."""
    if not strikes:
        raise ValueError("no strikes to choose from")
    if option_type == "call":
        target = spot * (1 + otm_offset)
    else:
        target = spot * (1 - otm_offset)
    return min(strikes, key=lambda k: abs(k - target))


def pick_by_delta(deltas: dict, target_delta: float) -> str:
    """From {option_symbol: delta}, pick the symbol whose delta is closest to
    target_delta. Use +0.5 for an ATM call, -0.5 for an ATM put, 0.3 for a
    30-delta OTM, etc."""
    if not deltas:
        raise ValueError("no deltas to choose from")
    return min(deltas, key=lambda s: abs(deltas[s] - target_delta))


def occ_type(option_symbol: str) -> str:
    """'call' or 'put' from an OCC symbol like 'AAPL240119C00100000'.
    Layout: <root><YYMMDD><C|P><8-digit strike> — so the type char sits 9
    from the end."""
    return "call" if option_symbol[-9].upper() == "C" else "put"


def occ_underlying(option_symbol: str) -> str:
    """Underlying root from an OCC symbol (everything before the final 15
    chars: 6 date + 1 type + 8 strike)."""
    return option_symbol[:-15].strip()


# ---- Alpaca-backed options client --------------------------------------

class AlpacaOptions:
    def __init__(self, paper=True):
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.historical.stock import StockHistoricalDataClient
        key = os.environ.get("ALPACA_API_KEY")
        secret = os.environ.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise SystemExit("Set ALPACA_API_KEY and ALPACA_SECRET_KEY env vars.")
        self.trading = TradingClient(key, secret, paper=paper)
        self.opt_data = OptionHistoricalDataClient(key, secret)
        self.stock_data = StockHistoricalDataClient(key, secret)

    def underlying_price(self, symbol: str) -> float:
        from alpaca.data.requests import StockLatestTradeRequest
        req = StockLatestTradeRequest(symbol_or_symbols=symbol)
        return float(self.stock_data.get_stock_latest_trade(req)[symbol].price)

    def find_contracts(self, underlying: str, option_type: str = "call",
                       dte_min: int = 25, dte_max: int = 45,
                       strike_low: float = None, strike_high: float = None):
        """Return the list of active contracts for an underlying within an
        expiry window (and optional strike band)."""
        from alpaca.trading.requests import GetOptionContractsRequest
        from alpaca.trading.enums import AssetStatus, ContractType
        today = date.today()
        ctype = ContractType.CALL if option_type == "call" else ContractType.PUT
        req = GetOptionContractsRequest(
            underlying_symbols=[underlying],
            status=AssetStatus.ACTIVE,
            type=ctype,
            expiration_date_gte=today + timedelta(days=dte_min),
            expiration_date_lte=today + timedelta(days=dte_max),
            strike_price_gte=str(strike_low) if strike_low else None,
            strike_price_lte=str(strike_high) if strike_high else None,
            limit=1000,
        )
        res = self.trading.get_option_contracts(req)
        return list(res.option_contracts)

    def select_atm(self, underlying: str, option_type: str = "call",
                   target_dte: int = 30, otm_offset: float = 0.0):
        """Select a contract nearest ATM (or a set % OTM) at the expiry
        closest to target_dte. Returns the contract object."""
        spot = self.underlying_price(underlying)
        band = 0.15  # search strikes within +/-15% of spot
        contracts = self.find_contracts(
            underlying, option_type,
            dte_min=max(1, target_dte - 12), dte_max=target_dte + 12,
            strike_low=spot * (1 - band), strike_high=spot * (1 + band))
        if not contracts:
            raise SystemExit(f"No {option_type} contracts found for {underlying}.")

        expiries = sorted({c.expiration_date for c in contracts})
        chosen_exp = pick_expiry(expiries, target_dte, date.today())
        at_exp = [c for c in contracts if c.expiration_date == chosen_exp]
        strikes = [float(c.strike_price) for c in at_exp]
        target_strike = pick_atm_strike(strikes, spot, otm_offset, option_type)
        return next(c for c in at_exp
                    if float(c.strike_price) == target_strike)

    def select_by_delta(self, underlying: str, option_type: str = "call",
                        target_dte: int = 30, target_delta: float = 0.5):
        """Select the contract whose delta is closest to target_delta, using
        the option chain's greeks. Needs the greeks feed; falls back to ATM
        if greeks are unavailable."""
        from alpaca.data.requests import OptionChainRequest
        from alpaca.trading.enums import ContractType
        ctype = ContractType.CALL if option_type == "call" else ContractType.PUT
        try:
            chain = self.opt_data.get_option_chain(
                OptionChainRequest(underlying_symbol=underlying, type=ctype))
            deltas = {}
            for sym, snap in chain.items():
                g = getattr(snap, "greeks", None)
                if g is not None and getattr(g, "delta", None) is not None:
                    deltas[sym] = float(g.delta)
            if deltas:
                tgt = target_delta if option_type == "call" else -abs(target_delta)
                return pick_by_delta(deltas, tgt)
        except Exception as e:
            print(f"  greeks unavailable ({e}); falling back to ATM strike.")
        return self.select_atm(underlying, option_type, target_dte).symbol

    def quote(self, option_symbol: str) -> dict:
        from alpaca.data.requests import OptionLatestQuoteRequest
        req = OptionLatestQuoteRequest(symbol_or_symbols=option_symbol)
        q = self.opt_data.get_option_latest_quote(req)[option_symbol]
        bid, ask = float(q.bid_price), float(q.ask_price)
        return {"bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 2)}

    def buy_to_open(self, option_symbol: str, qty: int = 1):
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        order = MarketOrderRequest(symbol=option_symbol, qty=qty,
                                   side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
        return self.trading.submit_order(order)

    def sell_to_close(self, option_symbol: str, qty: int = None):
        """Close a long option position. With qty=None, liquidates the whole
        position via ClosePositionRequest."""
        if qty is None:
            return self.trading.close_position(option_symbol)
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        order = MarketOrderRequest(symbol=option_symbol, qty=qty,
                                   side=OrderSide.SELL, time_in_force=TimeInForce.DAY)
        return self.trading.submit_order(order)

    def option_positions(self) -> list:
        """All current option positions (asset_class == 'us_option')."""
        out = []
        for p in self.trading.get_all_positions():
            if getattr(p, "asset_class", "") in ("us_option", "option"):
                out.append(p)
        return out
