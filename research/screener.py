"""
Cointegration screener for pairs trading.

Correlation is NOT cointegration. Two stocks can move together for years
(high correlation) yet never be tied to a stable spread — trade them as a
pair and the spread can wander off and never come back. Cointegration is
the real property you want: a linear combination of the two that is
stationary (mean-reverting). This module scans a basket, tests every pair,
and ranks the ones actually worth trading.

For each pair it reports:
  - coint_p     : Engle-Granger cointegration test p-value (lower = stronger;
                  < 0.05 is the usual threshold).
  - adf_p       : ADF stationarity test on the spread residual (confirms the
                  spread itself is mean-reverting).
  - half_life   : how many bars the spread takes to revert halfway to its
                  mean. Too short (< a few bars) is noise you'll pay away in
                  costs; too long (> your horizon) ties up capital. A sweet
                  spot for daily bars is roughly 5-40 days.
  - hedge_ratio : units of B per unit of A (OLS beta) — how to size the legs.
  - hedge_drift : how much a 60-bar ROLLING beta has wandered around the
                  full-sample beta (std of rolling betas / |beta|). Small is
                  good. A "cointegrated" pair whose hedge ratio drifts a lot
                  will quietly de-hedge on you in live trading — prefer a
                  rolling/Kalman hedge ratio for anything that goes live.
  - current_z   : where the spread sits right now in std-devs. |z| >= ~2
                  means the pair is stretched and potentially entry-ready.
  - corr        : plain price correlation, shown only as context.

IMPORTANT — multiple testing: scanning N names tests N*(N-1)/2 pairs, so a
few will clear p < 0.05 by pure chance. Treat the ranking as a shortlist to
investigate (does the economic link make sense? does it hold out-of-sample?),
never as an automatic green light.
"""

import warnings
import numpy as np
import pandas as pd
from itertools import combinations

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from statsmodels.tsa.stattools import coint, adfuller
    import statsmodels.api as sm


def half_life(spread: np.ndarray) -> float:
    """Half-life of mean reversion from an AR(1)/Ornstein-Uhlenbeck fit:
    regress ΔS_t on S_{t-1}; half-life = -ln(2) / slope."""
    s = np.asarray(spread, dtype=float)
    s_lag = s[:-1]
    ds = np.diff(s)
    X = sm.add_constant(s_lag)
    beta = sm.OLS(ds, X).fit().params[1]
    if beta >= 0:
        return np.inf  # not mean-reverting
    return float(-np.log(2) / beta)


def rolling_hedge_ratio(a: pd.Series, b: pd.Series, window: int = 60) -> pd.Series:
    """Rolling OLS beta of log(a) on log(b). This is what a LIVE pairs
    strategy should size its legs with — a static full-sample beta bakes
    look-ahead into the hedge and slowly de-hedges as the relationship
    evolves. (A Kalman filter is the smoother upgrade; rolling OLS is the
    simple, robust version.)"""
    common = a.index.intersection(b.index)
    la = np.log(a.loc[common].astype(float))
    lb = np.log(b.loc[common].astype(float))
    cov = la.rolling(window).cov(lb)
    var = lb.rolling(window).var()
    return (cov / var).rename("hedge_ratio")


def test_pair(a: pd.Series, b: pd.Series) -> dict | None:
    """Test one pair. a, b are close-price Series; they're aligned on their
    shared dates here. Returns a stats dict, or None if not enough data."""
    common = a.index.intersection(b.index)
    if len(common) < 60:
        return None
    a = a.loc[common].astype(float)
    b = b.loc[common].astype(float)
    if (a <= 0).any() or (b <= 0).any():
        return None

    la, lb = np.log(a.values), np.log(b.values)

    # OLS hedge ratio: la = alpha + beta*lb; spread = la - beta*lb.
    X = sm.add_constant(lb)
    beta = sm.OLS(la, X).fit().params[1]
    spread = la - beta * lb

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        coint_p = coint(la, lb)[1]
        adf_p = adfuller(spread, maxlag=1, autolag=None)[1]

    mu, sd = spread.mean(), spread.std()
    z = (spread[-1] - mu) / sd if sd > 0 else 0.0
    hl = half_life(spread)
    corr = float(np.corrcoef(a.values, b.values)[0, 1])

    # Hedge-ratio drift: std of rolling betas relative to the static beta.
    drift = np.nan
    if len(common) >= 120:
        rb = rolling_hedge_ratio(a, b, window=60).dropna()
        if len(rb) > 5 and abs(beta) > 1e-9:
            drift = float(rb.std() / abs(beta))

    return {
        "coint_p": round(float(coint_p), 4),
        "adf_p": round(float(adf_p), 4),
        "half_life": round(hl, 1) if np.isfinite(hl) else np.inf,
        "hedge_ratio": round(float(beta), 3),
        "hedge_drift": round(drift, 3) if np.isfinite(drift) else None,
        "current_z": round(float(z), 2),
        "corr": round(corr, 3),
        "n_obs": len(common),
    }


def screen(data: dict[str, pd.DataFrame],
           max_pvalue: float = 0.05,
           hl_min: float = 1.0, hl_max: float = 60.0) -> pd.DataFrame:
    """Test every pair in the basket. Returns a DataFrame ranked by
    cointegration p-value. A `tradeable` flag marks pairs that pass the
    p-value AND fall in the half-life band."""
    closes = {s: df["close"] for s, df in data.items()}
    rows = []
    for a, b in combinations(sorted(closes), 2):
        stats = test_pair(closes[a], closes[b])
        if stats is None:
            continue
        stats["pair"] = f"{a} / {b}"
        stats["a"], stats["b"] = a, b
        stats["tradeable"] = (
            stats["coint_p"] <= max_pvalue
            and hl_min <= stats["half_life"] <= hl_max
        )
        rows.append(stats)

    if not rows:
        return pd.DataFrame()
    cols = ["pair", "coint_p", "adf_p", "half_life", "hedge_ratio",
            "hedge_drift", "current_z", "corr", "n_obs", "tradeable", "a", "b"]
    df = pd.DataFrame(rows)[cols].sort_values("coint_p").reset_index(drop=True)
    return df


def summary_text(df: pd.DataFrame, max_pvalue: float = 0.05) -> str:
    if df.empty:
        return "No pairs could be tested (need >=60 overlapping bars each)."
    tradeable = df[df["tradeable"]]
    n_pairs = len(df)
    expected_false = n_pairs * max_pvalue
    show = df.drop(columns=["a", "b"]).head(15)
    lines = [
        show.to_string(index=False),
        "",
        f"Tested {n_pairs} pairs. {len(tradeable)} flagged tradeable "
        f"(coint_p <= {max_pvalue} and half-life in band).",
        f"Multiple-testing caveat: ~{expected_false:.1f} pairs could clear "
        f"p<{max_pvalue} by chance alone — verify the survivors before trusting them.",
    ]
    return "\n".join(lines)
