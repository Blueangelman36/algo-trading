"""
Performance metrics. These are what tell you whether a strategy is real
or just curve-fit noise. Pay most attention to:
  - Max drawdown: the worst peak-to-trough fall. This is what blows up
    accounts, not average return.
  - Sharpe: return per unit of volatility. Below ~1.0 annualized is weak;
    backtested Sharpes above ~2-3 usually mean you've overfit.
  - Trade count: a great-looking result from 4 trades is noise.
  - Exposure: a Sharpe of 1.2 while in the market 8% of the time is a very
    different animal from 1.2 fully invested. Low exposure inflates
    per-period stats and hides how little the strategy actually trades.
  - Calmar (CAGR / |max drawdown|): return per unit of the pain that
    actually ends accounts. > 1 is respectable for a retail system.
"""

import numpy as np
import pandas as pd


def _infer_periods_per_year(index: pd.DatetimeIndex) -> float:
    if len(index) < 3:
        return 252.0
    median_dt = pd.Series(index).diff().median()
    if pd.isna(median_dt) or median_dt.total_seconds() == 0:
        return 252.0
    seconds = median_dt.total_seconds()
    if seconds >= 23 * 3600:        # ~daily bars
        return 252.0
    if seconds >= 3600:             # hourly-ish; ~6.5h trading day
        return 252.0 * 6.5
    return 252.0 * 6.5 * 60.0 / max(1.0, seconds / 60.0)  # minute-ish


def _exposure_pct(portfolio) -> float:
    """Percent of marked bars with ANY open position, reconstructed by
    replaying fills against the equity-curve timestamps."""
    curve = portfolio.equity_curve
    if not curve:
        return 0.0
    fills = sorted(portfolio.fills, key=lambda f: f.timestamp)
    pos: dict[str, float] = {}
    fi, bars_in = 0, 0
    for ts, _ in curve:
        while fi < len(fills) and fills[fi].timestamp <= ts:
            f = fills[fi]
            pos[f.symbol] = pos.get(f.symbol, 0.0) + f.units
            if abs(pos[f.symbol]) < 1e-9:
                pos.pop(f.symbol, None)
            fi += 1
        if pos:
            bars_in += 1
    return 100.0 * bars_in / len(curve)


def compute_series(eq: pd.Series) -> dict:
    """Performance metrics from a bare equity Series (no fills needed).
    Used by the walk-forward harness to score arbitrary OOS slices."""
    if eq is None or len(eq) < 2:
        return {"error": "not enough data"}
    rets = eq.pct_change().dropna()
    ppy = _infer_periods_per_year(eq.index)

    total_return = eq.iloc[-1] / eq.iloc[0] - 1.0
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1.0 if eq.iloc[0] > 0 else np.nan

    vol = rets.std() * np.sqrt(ppy)
    sharpe = (rets.mean() * ppy) / (rets.std() * np.sqrt(ppy)) if rets.std() > 0 else np.nan
    downside = rets[rets < 0].std()
    sortino = (rets.mean() * ppy) / (downside * np.sqrt(ppy)) if downside and downside > 0 else np.nan

    running_max = eq.cummax()
    max_dd = (eq / running_max - 1.0).min()
    calmar = (cagr / abs(max_dd)) if (max_dd < 0 and not np.isnan(cagr)) else np.nan

    return {
        "start_equity": round(float(eq.iloc[0]), 2),
        "end_equity": round(float(eq.iloc[-1]), 2),
        "total_return_pct": round(float(total_return) * 100, 2),
        "cagr_pct": round(float(cagr) * 100, 2),
        "annualized_vol_pct": round(float(vol) * 100, 2),
        "sharpe": round(float(sharpe), 2) if not np.isnan(sharpe) else None,
        "sortino": round(float(sortino), 2) if not np.isnan(sortino) else None,
        "calmar": round(float(calmar), 2) if not np.isnan(calmar) else None,
        "max_drawdown_pct": round(float(max_dd) * 100, 2),
    }


def compute(portfolio) -> dict:
    eq = portfolio.equity_series()
    if eq.empty or len(eq) < 2:
        return {"error": "not enough data"}

    base = compute_series(eq)
    if "error" in base:
        return base
    base["num_fills"] = len(portfolio.fills)
    base["total_commission"] = round(sum(f.commission for f in portfolio.fills), 2)
    base["exposure_pct"] = round(_exposure_pct(portfolio), 1)
    base["periods_per_year_assumed"] = round(_infer_periods_per_year(eq.index), 1)
    return base


def summary_text(metrics: dict) -> str:
    if "error" in metrics:
        return f"Metrics error: {metrics['error']}"
    lines = [
        f"  Start equity:      ${metrics['start_equity']:,.2f}",
        f"  End equity:        ${metrics['end_equity']:,.2f}",
        f"  Total return:      {metrics['total_return_pct']:+.2f}%",
        f"  CAGR:              {metrics['cagr_pct']:+.2f}%",
        f"  Annualized vol:    {metrics['annualized_vol_pct']:.2f}%",
        f"  Sharpe:            {metrics['sharpe']}",
        f"  Sortino:           {metrics['sortino']}",
        f"  Calmar:            {metrics['calmar']}",
        f"  Max drawdown:      {metrics['max_drawdown_pct']:.2f}%",
    ]
    if "exposure_pct" in metrics:
        lines.append(f"  Exposure:          {metrics['exposure_pct']:.1f}% of bars")
    if "num_fills" in metrics:
        lines += [
            f"  Fills:             {metrics['num_fills']}",
            f"  Commission paid:   ${metrics['total_commission']:,.2f}",
        ]
    return "\n".join(lines)
