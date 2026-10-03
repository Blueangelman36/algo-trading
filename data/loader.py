"""
Data loading.

load_yf()  -> real OHLCV via yfinance. Works for stocks ("AAPL"), ETFs
             ("SPY", "USO" for oil exposure), and some futures continuous
             contracts ("CL=F" crude, "GC=F" gold, "ES=F" S&P). Futures
             history from yfinance is usable for prototyping but not
             production-grade — for serious futures backtests use a
             dedicated vendor (e.g. Databento, IBKR historical).

make_synthetic() -> deterministic fake data for testing the engine with
             no network. Generates a trending series and a mean-reverting
             series so both strategy types have something to bite on.
"""

import numpy as np
import pandas as pd


def load_yf(symbols, start, end=None, interval="1d") -> dict[str, pd.DataFrame]:
    """Fetch OHLCV. Requires `pip install yfinance`. Returns symbol -> df
    with columns open, high, low, close, volume (lowercased)."""
    import yfinance as yf
    if isinstance(symbols, str):
        symbols = [symbols]
    out = {}
    for sym in symbols:
        df = yf.download(sym, start=start, end=end, interval=interval,
                         auto_adjust=True, progress=False)
        if df.empty:
            print(f"  WARNING: no data for {sym}")
            continue
        # yfinance may return a MultiIndex column frame for single symbols
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        df.index = pd.to_datetime(df.index)
        out[sym] = df.dropna()
    return out


def make_synthetic(n=750, seed=7) -> dict[str, pd.DataFrame]:
    """Two synthetic instruments on a daily index:
       TREND  - geometric brownian motion with positive drift (trends)
       MEANREV - Ornstein-Uhlenbeck-like series that reverts to a level
    """
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-01", periods=n)

    # --- Trending series ---
    drift, vol = 0.0004, 0.012
    shocks = rng.normal(drift, vol, n)
    trend_close = 100 * np.exp(np.cumsum(shocks))

    # --- Mean-reverting series ---
    theta, mu, sigma = 0.05, 50.0, 0.9
    mr = np.empty(n)
    mr[0] = mu
    for t in range(1, n):
        mr[t] = mr[t-1] + theta * (mu - mr[t-1]) + rng.normal(0, sigma)
    mr_close = mr

    def to_ohlcv(close):
        close = np.asarray(close, dtype=float)
        noise = np.abs(rng.normal(0, 0.003, len(close))) * close
        high = close + noise
        low = close - noise
        open_ = np.concatenate([[close[0]], close[:-1]])
        vol = rng.integers(1_000_000, 5_000_000, len(close)).astype(float)
        return pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
            index=idx,
        )

    return {"TREND": to_ohlcv(trend_close), "MEANREV": to_ohlcv(mr_close)}


def make_cointegrated_pair(n=750, seed=11) -> dict[str, pd.DataFrame]:
    """Two synthetic instruments that share a common stochastic trend but
    whose *spread* mean-reverts — i.e. genuinely cointegrated, so pairs
    trading has something real to capture. PAIR_A = base + spread,
    PAIR_B = base, where base is a random walk and spread is an OU process
    reverting to zero.
    """
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-01", periods=n)

    base = 100 + np.cumsum(rng.normal(0, 0.6, n))     # common trend (random walk)

    theta, sigma = 0.08, 0.5                          # OU spread around 0
    spread = np.empty(n)
    spread[0] = 0.0
    for t in range(1, n):
        spread[t] = spread[t-1] * (1 - theta) + rng.normal(0, sigma)

    a_close = base + spread
    b_close = base.copy()

    def to_ohlcv(close):
        close = np.asarray(close, dtype=float)
        noise = np.abs(rng.normal(0, 0.002, len(close))) * close
        high = close + noise
        low = close - noise
        open_ = np.concatenate([[close[0]], close[:-1]])
        vol = rng.integers(1_000_000, 5_000_000, len(close)).astype(float)
        return pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
            index=idx,
        )

    return {"PAIR_A": to_ohlcv(a_close), "PAIR_B": to_ohlcv(b_close)}


def make_basket(n=750, seed=21) -> dict[str, pd.DataFrame]:
    """A basket for testing the cointegration screener: one genuinely
    cointegrated pair (COINT_X / COINT_Y) plus independent random walks
    (RAND1, RAND2, RAND3) that should NOT screen as cointegrated. A good
    screener ranks the real pair at the top and leaves the noise out."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-01", periods=n)

    def to_ohlcv(close):
        close = np.asarray(close, dtype=float)
        noise = np.abs(rng.normal(0, 0.002, len(close))) * close
        open_ = np.concatenate([[close[0]], close[:-1]])
        vol = rng.integers(1_000_000, 5_000_000, len(close)).astype(float)
        return pd.DataFrame(
            {"open": open_, "high": close + noise, "low": close - noise,
             "close": close, "volume": vol}, index=idx)

    # Cointegrated pair: shared trend + mean-reverting spread.
    base = 80 + np.cumsum(rng.normal(0, 0.5, n))
    theta, sigma = 0.06, 0.4
    sp = np.empty(n); sp[0] = 0.0
    for t in range(1, n):
        sp[t] = sp[t-1] * (1 - theta) + rng.normal(0, sigma)
    out = {"COINT_X": to_ohlcv(base + sp), "COINT_Y": to_ohlcv(base)}

    # Independent random walks — no cointegration with anything.
    for name in ("RAND1", "RAND2", "RAND3"):
        rw = 50 + np.cumsum(rng.normal(0.02, 0.8, n))
        rw = np.maximum(rw, 1.0)
        out[name] = to_ohlcv(rw)
    return out
