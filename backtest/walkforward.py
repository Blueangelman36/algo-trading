"""
Walk-forward analysis — the real defense against overfitting.

A plain backtest lets you tune parameters on the *same* history you judge
them on, so of course they look great — you fit them to that history. Walk
forward instead:

    [-- train --][ test ]
         [-- train --][ test ]
              [-- train --][ test ]

On each fold it optimizes the strategy's parameters on the TRAIN window,
then measures them on the immediately following TEST window the strategy has
never seen. Stitching the test windows together gives an out-of-sample (OOS)
equity curve — an honest estimate of what you'd actually have earned.

The number that matters is the gap between in-sample (IS) and OOS results.
If IS Sharpe is 2.5 and OOS is 0.1, the strategy is curve-fit garbage no
matter how good the full-history backtest looked. A strategy whose OOS holds
up near its IS is one worth trusting.

NEW — parameter stability: the OTHER overfitting tell. If fold 1 picks
lookback=10 and fold 2 picks lookback=30 and fold 3 picks lookback=20, the
"optimal" parameter is noise and you should distrust the whole result even
if the OOS numbers look fine. The summary now reports how consistently each
parameter value gets chosen across folds.

Modes:
  - rolling (default): fixed-size train window slides forward.
  - anchored: train window grows from a fixed start (more data each fold).
"""

import itertools
from collections import Counter
import numpy as np
import pandas as pd

from backtest.engine import BacktestEngine
from backtest.portfolio import Portfolio
from backtest import metrics as M
from strategies.mean_reversion import MeanReversion
from strategies.momentum import Momentum
from strategies.pairs import PairsTrading


STRATEGIES = {
    "meanrev": MeanReversion, "mean_reversion": MeanReversion,
    "momentum": Momentum, "trend": Momentum,
    "pairs": PairsTrading, "statarb": PairsTrading,
}

# Sensible parameter grids to search on each train window.
DEFAULT_GRIDS = {
    "meanrev": {"lookback": [10, 20, 30], "entry_z": [1.5, 2.0, 2.5], "exit_z": [0.5]},
    "momentum": {"fast": [10, 20], "slow": [50, 100], "allow_short": [True]},
    "pairs": {"lookback": [30, 60, 90], "entry_z": [1.5, 2.0, 2.5], "exit_z": [0.5]},
}

# New strategy modules register themselves if present (so this file still
# imports cleanly before you've copied them in).
try:
    from strategies.xsec_momentum import CrossSectionalMomentum
    STRATEGIES["xsec"] = CrossSectionalMomentum
    DEFAULT_GRIDS["xsec"] = {"lookback": [63, 126, 189], "skip": [5],
                             "n_long": [1, 2], "n_short": [0], "rebalance": [21]}
except ImportError:
    pass
try:
    from strategies.breakout import Breakout
    STRATEGIES["breakout"] = Breakout
    DEFAULT_GRIDS["breakout"] = {"entry": [20, 55], "exit": [10, 20],
                                 "allow_short": [True]}
except ImportError:
    pass


def _warmup_for(params: dict) -> int:
    lb = max(params.get("lookback", 0), params.get("slow", 0),
             params.get("breakout", 0), params.get("entry", 0),
             params.get("lookback", 0) + params.get("skip", 0))
    return max(50, lb + 5)


def _run(strategy_cls, symbols, data, params, starting_cash, max_gross,
         commission, slippage):
    warmup = _warmup_for(params)
    strat = strategy_cls(symbols, **params)
    pf = Portfolio(starting_cash=starting_cash, commission_per_share=commission,
                   slippage_bps=slippage)
    BacktestEngine(strat, data, portfolio=pf, max_gross_per_symbol=max_gross,
                   warmup=warmup).run()
    return pf


def _score(pf, metric) -> float:
    m = M.compute(pf)
    if "error" in m:
        return float("-inf")
    val = m.get({"sharpe": "sharpe", "return": "total_return_pct",
                 "sortino": "sortino"}.get(metric, "sharpe"))
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return float("-inf")
    return float(val)


def optimize(strategy_cls, symbols, train_data, grid, metric,
             starting_cash, max_gross, commission, slippage):
    """Grid-search params on the train window; return (best_params, best_score)."""
    keys = list(grid.keys())
    best_params, best_score = None, float("-inf")
    for combo in itertools.product(*grid.values()):
        params = dict(zip(keys, combo))
        pf = _run(strategy_cls, symbols, train_data, params,
                  starting_cash, max_gross, commission, slippage)
        s = _score(pf, metric)
        if s > best_score:
            best_params, best_score = params, s
    return best_params or dict(zip(keys, [g[0] for g in grid.values()])), best_score


def param_stability(folds: list) -> dict:
    """For each tuned parameter: the most-picked value and how often it was
    picked. A mode share below ~50% means the optimizer is chasing noise."""
    keys = set()
    for f in folds:
        keys.update(f["best_params"])
    out = {}
    for k in sorted(keys):
        vals = [f["best_params"][k] for f in folds if k in f["best_params"]]
        if not vals:
            continue
        counts = Counter(vals)
        mode, n = counts.most_common(1)[0]
        out[k] = {"mode": mode, "share": n / len(vals),
                  "counts": dict(counts)}
    return out


def walk_forward(data, symbols, strategy_name, train_bars, test_bars,
                 grid=None, metric="sharpe", anchored=False,
                 starting_cash=100_000.0, commission=0.005, slippage_bps=1.0):
    strategy_cls = STRATEGIES[strategy_name]
    if grid is None:
        grid = DEFAULT_GRIDS.get(strategy_name) or DEFAULT_GRIDS[
            strategy_name.replace("_reversion", "rev")
                         .replace("trend", "momentum")
                         .replace("statarb", "pairs")]
    n_sym = len(symbols)
    max_gross = 1.0 / n_sym

    # Master timeline (union of all symbols' bars).
    idx = None
    for df in data.values():
        idx = df.index if idx is None else idx.union(df.index)
    timeline = idx.sort_values()
    n = len(timeline)

    folds = []
    oos_pieces = []
    running_equity = starting_cash
    fold = 0

    while True:
        train_start = 0 if anchored else fold * test_bars
        train_end = (train_bars + fold * test_bars) if anchored else (train_start + train_bars)
        test_start = train_end
        test_end = test_start + test_bars
        if test_end > n or train_end <= train_start:
            break

        t_tr0, t_tr1 = timeline[train_start], timeline[train_end - 1]
        t_te0, t_te1 = timeline[test_start], timeline[test_end - 1]

        train_data = {s: df.loc[t_tr0:t_tr1] for s, df in data.items()}

        best_params, is_score = optimize(
            strategy_cls, symbols, train_data, grid, metric,
            starting_cash, max_gross, commission, slippage_bps)

        # OOS: prime indicators with a warmup lead-in before the test window,
        # then trade through the test window starting from running equity.
        warmup = _warmup_for(best_params)
        lead = max(0, test_start - warmup)
        t_lead = timeline[lead]
        test_data = {s: df.loc[t_lead:t_te1] for s, df in data.items()}
        pf_oos = _run(strategy_cls, symbols, test_data, best_params,
                      running_equity, max_gross, commission, slippage_bps)

        # Keep only the equity from the true test start onward (drop the flat
        # warmup lead-in) and chain it onto the running OOS curve.
        eq = pf_oos.equity_series()
        eq = eq.loc[eq.index >= t_te0]
        if len(eq) >= 2:
            oos_pieces.append(eq)
            running_equity = float(eq.iloc[-1])
        oos_m = M.compute_series(eq)

        folds.append({
            "fold": fold + 1,
            "train": f"{t_tr0:%Y-%m-%d}..{t_tr1:%Y-%m-%d}",
            "test": f"{t_te0:%Y-%m-%d}..{t_te1:%Y-%m-%d}",
            "best_params": best_params,
            "is_score": round(is_score, 2) if np.isfinite(is_score) else None,
            "oos_sharpe": oos_m.get("sharpe"),
            "oos_return_pct": oos_m.get("total_return_pct"),
            "oos_maxdd_pct": oos_m.get("max_drawdown_pct"),
        })
        fold += 1

    stitched = pd.concat(oos_pieces) if oos_pieces else pd.Series(dtype=float)
    stitched = stitched[~stitched.index.duplicated(keep="first")].sort_index()
    return {"folds": folds, "oos_equity": stitched,
            "oos_metrics": M.compute_series(stitched) if len(stitched) > 2 else {},
            "param_stability": param_stability(folds),
            "metric": metric, "anchored": anchored}


def summary_text(result: dict) -> str:
    folds = result["folds"]
    if not folds:
        return ("No folds ran — history too short for the chosen train/test "
                "sizes. Try smaller --train / --test.")
    lines = [f"Walk-forward ({'anchored' if result['anchored'] else 'rolling'}, "
             f"optimizing {result['metric']} in-sample):", ""]
    header = f"  {'fold':<5}{'test window':<26}{'OOS ret%':>9}{'OOS Sharpe':>12}{'OOS maxDD%':>12}  params"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    is_scores, oos_sharpes = [], []
    for f in folds:
        p = ",".join(f"{k}={v}" for k, v in f["best_params"].items())
        lines.append(f"  {f['fold']:<5}{f['test']:<26}"
                     f"{(f['oos_return_pct'] or 0):>+9.2f}"
                     f"{(f['oos_sharpe'] if f['oos_sharpe'] is not None else 0):>12.2f}"
                     f"{(f['oos_maxdd_pct'] or 0):>12.2f}  {p}")
        if f["is_score"] is not None:
            is_scores.append(f["is_score"])
        if f["oos_sharpe"] is not None:
            oos_sharpes.append(f["oos_sharpe"])

    m = result["oos_metrics"]
    lines += ["", "  Stitched out-of-sample (the honest result):"]
    if m and "error" not in m:
        lines += [
            f"    Total return:   {m['total_return_pct']:+.2f}%",
            f"    CAGR:           {m['cagr_pct']:+.2f}%",
            f"    Sharpe:         {m['sharpe']}",
            f"    Max drawdown:   {m['max_drawdown_pct']:.2f}%",
        ]
    if is_scores and oos_sharpes:
        avg_is, avg_oos = np.mean(is_scores), np.mean(oos_sharpes)
        lines += ["",
                  f"  Overfitting check: avg IS {result['metric']} {avg_is:.2f} "
                  f"vs avg OOS Sharpe {avg_oos:.2f}."]
        if avg_is > 0:
            eff = avg_oos / avg_is
            verdict = ("holds up well" if eff >= 0.5 else
                       "degrades a lot — likely overfit" if eff < 0.25 else
                       "degrades somewhat")
            lines.append(f"  Walk-forward efficiency ~{eff:.0%} ({verdict}).")

    stab = result.get("param_stability") or {}
    if stab and len(folds) >= 3:
        lines += ["", "  Parameter stability (mode share across folds; "
                      "<50% = optimizer chasing noise):"]
        for k, v in stab.items():
            flag = "  <-- unstable" if v["share"] < 0.5 else ""
            lines.append(f"    {k:<12} picks {v['counts']}  "
                         f"(mode {v['mode']} in {v['share']:.0%} of folds){flag}")
    return "\n".join(lines)
