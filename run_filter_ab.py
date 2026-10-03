"""
run_filter_ab.py — A/B test: the SAME strategy on the SAME data, with and
without the catalyst filters, compared side by side.

This answers the only question that matters about the safety net: is it
actually buying you anything? Per the docs, what "good" looks like is fewer
trades and meaningfully smaller worst losses / max drawdown, at a similar or
slightly lower return — insurance, and insurance has a premium. What "bad"
looks like is returns collapsing while drawdown barely improves — the filter
is eating good trades.

Examples:
  # Baseline vs event blackouts (EIA/OPEC) on USO mean reversion:
  python run_filter_ab.py --symbols USO --strategy meanrev --start 2023-01-01 --events

  # Pairs on two energy names, standing aside around earnings + 8-Ks:
  python run_filter_ab.py --symbols XOM CVX --strategy pairs --start 2022-01-01 \
      --earnings --sec

  # Everything at once, with a news cache from run_news_fetch.py:
  python run_filter_ab.py --symbols USO --strategy meanrev --start 2023-01-01 \
      --events --news-cache news_oil.json

DAILY-BAR WINDOWS (important, read once):
  Daily bars are stamped at midnight, but the EIA report window is ±minutes
  around 10:30 ET — a midnight timestamp never falls inside it, so on daily
  data the event filter would silently never fire. When the data is daily,
  this script therefore widens the event window to blanket the whole event
  DAY, and widens the news-intensity window to 24h. That is the correct
  daily-bar interpretation of "don't trade on report day". Intraday backtests
  (if you ever store intraday history) use the normal minute windows.

Requires the SEC_USER_AGENT env var if --earnings or --sec is used, and
ALPACA keys are NOT needed (news comes from the offline cache).

Paper research tooling. Not financial advice.
"""

import argparse
import pandas as pd

from loader import load_yf
from backtest.engine import BacktestEngine
from backtest.portfolio import Portfolio
from backtest import metrics as M
from backtest import trades as T


# ---- strategy factory (same names as run_paper.py) -----------------------
def build_strategy(name, symbols):
    if name in ("meanrev", "mean_reversion"):
        from strategies.mean_reversion import MeanReversion
        return MeanReversion(symbols)
    if name in ("momentum", "trend"):
        from strategies.momentum import Momentum
        return Momentum(symbols)
    if name in ("pairs", "statarb"):
        from strategies.pairs import PairsTrading
        return PairsTrading(symbols)
    if name == "breakout":
        from strategies.breakout import Breakout
        return Breakout(symbols)
    if name == "pyramid":
        from strategies.pyramiding_breakout import PyramidingBreakout
        return PyramidingBreakout(symbols)
    if name == "xsec":
        from strategies.xsec_momentum import CrossSectionalMomentum
        return CrossSectionalMomentum(symbols)
    raise SystemExit(f"Unknown strategy: {name}")


def warmup_for(strat) -> int:
    vals = [0]
    params = getattr(strat, "params", None)
    if isinstance(params, dict):
        vals += [int(params.get(k, 0) or 0) for k in ("lookback", "slow", "entry")]
    for k in ("lookback", "slow", "entry", "window", "atr_window"):
        v = getattr(strat, k, 0)
        if isinstance(v, (int, float)):
            vals.append(int(v))
    return max(50, max(vals) + 5)


# ---- filter construction --------------------------------------------------
def wrap_with_filters(strat, args, daily: bool):
    """Build the same filter stack run_paper.py uses, but with the offline
    news cache and daily-appropriate windows."""
    from strategies.news_filter import NewsFilter
    from data.news import CachedNews, NullNews

    cal = None
    if args.events:
        from research.event_calendar import EventCalendar
        if daily:
            # Blanket the event's whole calendar day (see module docstring).
            cal = EventCalendar(before_min=20 * 60, after_min=10 * 60,
                                opec_before_min=24 * 60, opec_after_min=24 * 60)
        else:
            cal = EventCalendar()

    extra = []
    if args.earnings:
        from research.earnings_calendar import EarningsCalendar
        from research.sec_filings import EdgarClient
        extra.append(EarningsCalendar(edgar_client=EdgarClient(),
                                      before_days=1, after_days=1))
    if args.sec:
        from research.sec_filings import EdgarClient, SecVetoSource
        extra.append(SecVetoSource(EdgarClient(), lookback_days=1))

    news = CachedNews(args.news_cache) if args.news_cache else NullNews()
    window = 24 * 60 if daily else 120
    return NewsFilter(strat, news_provider=news, calendar=cal,
                      news_window_min=window, extra_sources=extra,
                      verbose=False)


# ---- one run ---------------------------------------------------------------
def run_one(strat, data, args, max_gross):
    pf = Portfolio(starting_cash=args.cash,
                   commission_per_share=args.commission,
                   slippage_bps=args.slippage)
    BacktestEngine(strat, data, portfolio=pf, max_gross_per_symbol=max_gross,
                   warmup=warmup_for(getattr(strat, "inner", strat)),
                   fill_mode=args.fill_mode).run()
    return pf


def collect(pf) -> dict:
    m = M.compute(pf)
    # Mark any still-open position to its last fill price so worst-trade
    # stats are complete.
    last_prices = {}
    for f in pf.fills:
        last_prices[f.symbol] = f.price
    tr = T.extract_trades(pf.fills, last_prices)
    ts = T.trade_stats(tr)
    return {
        "return_pct": m.get("total_return_pct"),
        "sharpe": m.get("sharpe"),
        "calmar": m.get("calmar"),
        "max_dd_pct": m.get("max_drawdown_pct"),
        "exposure_pct": m.get("exposure_pct"),
        "trades": ts.get("num_trades", 0),
        "win_rate": ts.get("win_rate_pct"),
        "worst_trade": ts.get("largest_loss"),
        "avg_loss": ts.get("avg_loss"),
        "expectancy": ts.get("expectancy_per_trade"),
        "commission": m.get("total_commission"),
    }


def fmt(v, pct=False, money=False):
    if v is None:
        return "n/a"
    if money:
        return f"${v:,.2f}"
    if pct:
        return f"{v:+.2f}%"
    return f"{v}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", required=True)
    p.add_argument("--strategy", default="meanrev")
    p.add_argument("--start", default="2022-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--interval", default="1d",
                   help="yfinance interval; 1d is the default and recommended")
    # Which filters to A/B (at least one; default --events if none given):
    p.add_argument("--events", action="store_true")
    p.add_argument("--news-cache", default=None)
    p.add_argument("--earnings", action="store_true")
    p.add_argument("--sec", action="store_true")
    # Costs / engine:
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--commission", type=float, default=0.005)
    p.add_argument("--slippage", type=float, default=1.0)
    p.add_argument("--fill-mode", default="close", choices=["close", "next_open"])
    args = p.parse_args()

    if not any([args.events, args.news_cache, args.earnings, args.sec]):
        print("No filter flags given — defaulting to --events.")
        args.events = True
    if args.strategy in ("pairs", "statarb") and len(args.symbols) != 2:
        raise SystemExit("Pairs needs exactly 2 symbols.")

    daily = args.interval.lower() in ("1d", "1day")
    print(f"Loading {args.symbols} from {args.start} "
          f"({args.interval}, fill={args.fill_mode}) ...")
    data = load_yf(args.symbols, args.start, args.end, interval=args.interval)
    missing = [s for s in args.symbols if s not in data]
    if missing:
        raise SystemExit(f"No data for {missing}.")
    max_gross = 1.0 / len(args.symbols)

    # ---- run A (baseline) and B (filtered) on identical data -------------
    base_strat = build_strategy(args.strategy, args.symbols)
    filt_strat = wrap_with_filters(build_strategy(args.strategy, args.symbols),
                                   args, daily)

    print("Running baseline ...")
    pf_a = run_one(base_strat, data, args, max_gross)
    print("Running filtered ...")
    pf_b = run_one(filt_strat, data, args, max_gross)

    a, b = collect(pf_a), collect(pf_b)
    vetoes = filt_strat.veto_report()

    # ---- side-by-side ------------------------------------------------------
    filters_on = [n for n, on in [("events", args.events),
                                  ("news", bool(args.news_cache)),
                                  ("earnings", args.earnings),
                                  ("sec", args.sec)] if on]
    rows = [
        ("Total return",   fmt(a["return_pct"], pct=True),  fmt(b["return_pct"], pct=True)),
        ("Sharpe",         fmt(a["sharpe"]),                fmt(b["sharpe"])),
        ("Calmar",         fmt(a["calmar"]),                fmt(b["calmar"])),
        ("Max drawdown",   fmt(a["max_dd_pct"], pct=True),  fmt(b["max_dd_pct"], pct=True)),
        ("Worst trade",    fmt(a["worst_trade"], money=True), fmt(b["worst_trade"], money=True)),
        ("Avg loss",       fmt(a["avg_loss"], money=True),  fmt(b["avg_loss"], money=True)),
        ("Expectancy/trade", fmt(a["expectancy"], money=True), fmt(b["expectancy"], money=True)),
        ("Win rate",       fmt(a["win_rate"]),              fmt(b["win_rate"])),
        ("Trades",         fmt(a["trades"]),                fmt(b["trades"])),
        ("Exposure",       fmt(a["exposure_pct"]),          fmt(b["exposure_pct"])),
        ("Commission",     fmt(a["commission"], money=True), fmt(b["commission"], money=True)),
    ]
    w = 18
    print(f"\n{args.strategy} on {' '.join(args.symbols)} — "
          f"baseline vs filtered [{', '.join(filters_on)}]\n")
    print(f"  {'':<18}{'baseline':>{w}}{'filtered':>{w}}")
    print("  " + "-" * (18 + 2 * w))
    for name, va, vb in rows:
        print(f"  {name:<18}{va:>{w}}{vb:>{w}}")
    print(f"\n  Vetoed entries: {len(vetoes)}")
    if len(vetoes):
        print(vetoes.tail(8).to_string(index=False))

    # ---- verdict -------------------------------------------------------------
    print("\n  Verdict:")
    if len(vetoes) == 0:
        print("    The filter never fired in this window — nothing to conclude.")
        print("    Either the period had no covered catalysts hitting entries,")
        print("    or (daily data) check the symbols match the event calendar's lists.")
        return

    dd_a, dd_b = a["max_dd_pct"] or 0.0, b["max_dd_pct"] or 0.0
    ret_a, ret_b = a["return_pct"] or 0.0, b["return_pct"] or 0.0
    wt_a, wt_b = abs(a["worst_trade"] or 0.0), abs(b["worst_trade"] or 0.0)

    dd_improve = dd_b - dd_a            # drawdowns negative: positive = better
    ret_cost = ret_a - ret_b            # positive = filter cost return
    wt_improve = wt_a - wt_b            # positive = smaller worst loss

    print(f"    Drawdown change: {dd_improve:+.2f}pp | return cost: "
          f"{ret_cost:+.2f}pp | worst-trade change: ${wt_improve:+,.2f}")
    if dd_improve > 1.0 and (ret_cost <= dd_improve or ret_cost <= 2.0):
        print("    -> Filter is doing its job: meaningfully smaller drawdown for an")
        print("       acceptable insurance premium. Walk-forward it before trusting it.")
    elif ret_cost > 2.0 and dd_improve < 1.0:
        print("    -> Filter is eating good trades: returns fell without drawdown")
        print("       improving. Loosen the windows (before/after) or drop it.")
    elif abs(dd_improve) < 0.5 and abs(ret_cost) < 0.5:
        print("    -> Barely any difference. The catalysts weren't hurting this")
        print("       strategy in this window; the filter is optional here.")
    else:
        print("    -> Mixed result. Look at the worst-trade and avg-loss lines —")
        print("       if those improved, the tail protection may still be worth the")
        print("       return cost. Try a longer window before deciding.")
    print("\n  Same rule as everything else: the walk-forward gate doesn't move.")


if __name__ == "__main__":
    main()
