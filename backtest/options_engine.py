"""
Model-based options backtester.

Free historical option prices basically don't exist, so this doesn't replay
real option quotes — it PRICES options with Black-Scholes off the underlying's
real price history and an estimated volatility. A directional strategy runs on
the underlying (the same Strategy classes as everywhere else); on a bullish
signal it buys a call, bearish a put, and the position is repriced every bar as
the underlying moves and time decays, then settled at expiry or closed on a
signal flip.

WHAT THIS CAPTURES (usefully):
  - directional exposure (delta) and how it changes,
  - time decay / theta — the tax that makes "right on direction, wrong on
    timing" still lose,
  - the effect of your DTE and moneyness choices.

WHAT IT DOES NOT (be honest):
  - real bid/ask spreads (approximated by a flat % haircut),
  - the volatility smile / skew (single IV per bar),
  - IV changes over time / vega P&L (IV is modeled, not market),
  - early assignment, liquidity, gaps.
Treat results as directional intuition and relative comparison, NOT precise
P&L. This is not financial advice.

NEW — roll tracking: when a near-expiry close is immediately followed by
re-opening the same direction (the signal is still on), that's a roll, and
every roll pays the spread haircut + commissions again. trade_stats now
counts rolls so you can see how much P&L drag is roll friction vs being
wrong on direction. If rolls dominate your costs, raise target_dte.
"""

from dataclasses import dataclass
import numpy as np
import pandas as pd

from strategies.base import Context
from backtest import blackscholes as bs
from backtest import metrics as M

CONTRACT_MULTIPLIER = 100  # one option = 100 shares


@dataclass
class OptionPosition:
    kind: str            # 'call' or 'put'
    strike: float
    expiry: pd.Timestamp
    qty: int
    entry_price: float   # per-share premium paid
    entry_date: pd.Timestamp
    entry_underlying: float


@dataclass
class OptionTrade:
    kind: str
    strike: float
    qty: int
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    pnl: float           # net of commissions, dollars
    reason: str          # 'signal' | 'expiry' | 'end'
    held_days: int


def _signal(w):
    if w is None:
        return "hold"
    return "bull" if w > 0.5 else "bear" if w < -0.5 else "flat"


class OptionsBacktest:
    def __init__(self, strategy, underlying, data, starting_cash=100_000.0,
                 target_dte=30, otm_offset=0.0, qty=1, min_dte_exit=5,
                 allow_puts=True, iv_mode="realized", iv=0.30,
                 iv_premium=1.0, vol_window=20, r=0.04,
                 commission_per_contract=0.65, spread_pct=0.01, warmup=55):
        self.strategy = strategy
        self.u = underlying
        self.df = data[underlying]
        self.cash = starting_cash
        self.start_cash = starting_cash
        self.target_dte = target_dte
        self.otm = otm_offset
        self.qty = qty
        self.min_dte_exit = min_dte_exit
        self.allow_puts = allow_puts
        self.iv_mode = iv_mode
        self.iv_fixed = iv
        self.iv_premium = iv_premium
        self.vol_window = vol_window
        self.r = r
        self.commission = commission_per_contract
        self.spread_pct = spread_pct
        self.warmup = warmup

        self.pos: OptionPosition | None = None
        self.trades: list[OptionTrade] = []
        self.equity_curve: list[tuple] = []
        self.rolls = 0
        self._last_expiry_close = None   # (date, kind) of the last expiry exit

    # ---- volatility estimate -------------------------------------------
    def _iv(self, hist_close: pd.Series) -> float:
        if self.iv_mode == "fixed":
            return self.iv_fixed
        rets = np.log(hist_close).diff().dropna()
        w = rets.iloc[-self.vol_window:]
        if len(w) < 5:
            return self.iv_fixed
        rv = float(w.std() * np.sqrt(252))
        return max(0.05, rv * self.iv_premium)

    def _T(self, today, expiry) -> float:
        return max((expiry - today).days, 0) / 365.0

    def _fill_price(self, mid, buying: bool) -> float:
        """Apply a flat spread haircut: pay above mid to buy, receive below
        mid to sell."""
        half = self.spread_pct / 2.0
        return mid * (1 + half) if buying else mid * (1 - half)

    # ---- position lifecycle --------------------------------------------
    def _open(self, kind, today, S, iv):
        # Same-day, same-direction re-open after an expiry exit == a roll.
        if self._last_expiry_close == (today, kind):
            self.rolls += 1
        expiry = today + pd.Timedelta(days=self.target_dte)
        if kind == "call":
            strike = round(S * (1 + self.otm))
        else:
            strike = round(S * (1 - self.otm))
        strike = max(1.0, float(strike))
        T = self._T(today, expiry)
        mid = bs.price(S, strike, T, iv, r=self.r, kind=kind)
        fill = self._fill_price(mid, buying=True)
        cost = fill * CONTRACT_MULTIPLIER * self.qty + self.commission * self.qty
        self.cash -= cost
        self.pos = OptionPosition(kind, strike, expiry, self.qty, fill, today, S)

    def _close(self, today, S, iv, reason):
        p = self.pos
        T = self._T(today, p.expiry)
        mid = bs.price(S, p.strike, T, iv, r=self.r, kind=p.kind)
        fill = self._fill_price(mid, buying=False) if T > 0 else \
            bs._intrinsic(S, p.strike, p.kind)  # settle at intrinsic at expiry
        proceeds = fill * CONTRACT_MULTIPLIER * p.qty - self.commission * p.qty
        self.cash += proceeds
        pnl = (fill - p.entry_price) * CONTRACT_MULTIPLIER * p.qty \
            - self.commission * p.qty * 2
        self.trades.append(OptionTrade(
            p.kind, p.strike, p.qty, p.entry_date, today, p.entry_price, fill,
            round(pnl, 2), reason, (today - p.entry_date).days))
        if reason == "expiry":
            self._last_expiry_close = (today, p.kind)
        self.pos = None

    def _position_value(self, today, S, iv) -> float:
        if self.pos is None:
            return 0.0
        p = self.pos
        T = self._T(today, p.expiry)
        mid = bs.price(S, p.strike, T, iv, r=self.r, kind=p.kind)
        return mid * CONTRACT_MULTIPLIER * p.qty

    # ---- main loop ------------------------------------------------------
    def run(self):
        idx = self.df.index
        for i, today in enumerate(idx):
            S = float(self.df.at[today, "close"])
            hist = self.df["close"].loc[:today]
            iv = self._iv(hist)

            # Mark equity (cash + current option value) every bar.
            self.equity_curve.append((today, self.cash + self._position_value(today, S, iv)))

            if i < self.warmup:
                continue

            # Expiry / near-expiry management first.
            if self.pos is not None:
                dte = (self.pos.expiry - today).days
                if dte <= self.min_dte_exit:
                    self._close(today, S, iv, "expiry")

            ctx = Context(history={self.u: self.df.loc[:today]},
                          positions={self.u: 0.0}, cash=self.cash,
                          equity=self.equity_curve[-1][1])
            sig = _signal(self.strategy.on_bar(ctx).get(self.u))

            held = self.pos.kind if self.pos else None
            if sig == "bull":
                if held == "put":
                    self._close(today, S, iv, "signal")
                    held = None
                if held != "call":
                    self._open("call", today, S, iv)
            elif sig == "bear":
                if held == "call":
                    self._close(today, S, iv, "signal")
                    held = None
                if self.allow_puts and held != "put":
                    self._open("put", today, S, iv)
                elif held == "put":
                    pass
            elif sig == "flat":
                if self.pos is not None:
                    self._close(today, S, iv, "signal")

        # Close any position at the end for a complete accounting.
        if self.pos is not None:
            last = idx[-1]
            S = float(self.df.at[last, "close"])
            iv = self._iv(self.df["close"])
            self._close(last, S, iv, "end")
            # re-mark final equity
            self.equity_curve[-1] = (last, self.cash)
        return self

    def equity_series(self) -> pd.Series:
        if not self.equity_curve:
            return pd.Series(dtype=float)
        ts, eq = zip(*self.equity_curve)
        return pd.Series(eq, index=pd.DatetimeIndex(ts), name="equity")

    # ---- reporting ------------------------------------------------------
    def metrics(self) -> dict:
        return M.compute_series(self.equity_series())

    def trade_stats(self) -> dict:
        t = self.trades
        if not t:
            return {"num_trades": 0}
        wins = [x for x in t if x.pnl > 0]
        losses = [x for x in t if x.pnl < 0]
        gw = sum(x.pnl for x in wins)
        gl = sum(x.pnl for x in losses)
        reasons = {}
        for x in t:
            reasons[x.reason] = reasons.get(x.reason, 0) + 1
        return {
            "num_trades": len(t),
            "calls": sum(1 for x in t if x.kind == "call"),
            "puts": sum(1 for x in t if x.kind == "put"),
            "win_rate_pct": round(len(wins) / len(t) * 100, 1),
            "avg_win": round(gw / len(wins), 2) if wins else 0.0,
            "avg_loss": round(gl / len(losses), 2) if losses else 0.0,
            "profit_factor": round(gw / abs(gl), 2) if gl != 0 else None,
            "total_pnl": round(sum(x.pnl for x in t), 2),
            "avg_held_days": round(np.mean([x.held_days for x in t]), 1),
            "exit_reasons": reasons,
            "rolls": self.rolls,
        }
