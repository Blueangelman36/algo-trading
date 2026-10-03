"""
Run the live PAPER-trading bot. Fake money, real market data/mechanics.

Choose your broker with --broker:

  ALPACA (stocks / ETFs, incl. oil ETFs like USO):
    pip install alpaca-py
    set ALPACA_API_KEY / ALPACA_SECRET_KEY   (paper keys from alpaca.markets)
    python run_paper.py --broker alpaca --symbols USO --strategy meanrev --interval 5Min

  IBKR (futures / commodities, incl. crude CL):
    pip install ib_async, and run IB Gateway/TWS logged into your PAPER account
    python run_paper.py --broker ibkr --symbols CL --strategy momentum \
        --interval 5Min --ib-port 7497 --ib-sectype FUT --ib-exchange NYMEX

  Pairs trading (two symbols, market-neutral):
    python run_paper.py --broker alpaca --symbols USO BNO --strategy pairs --interval 15Min

CHANGES vs previous version:
  - New strategies: breakout (Donchian), pyramid (Turtle-style scale-in),
    xsec (cross-sectional momentum; needs 3+ symbols).
  - --vol-target 0.15 : volatility-targeted sizing (shared sizing.py module,
    identical math to the backtest engine). Blank/absent = fixed sizing.
  - --regime-filter : wraps the strategy in the efficiency-ratio veto
    (mode picked automatically: trend strategies veto chop, mean-rev
    strategies veto trends).
  - Lookback inference no longer assumes every strategy exposes .params
    (the new strategies don't) — it reads whichever attributes exist.
  - The four catalyst-filter flags are DEFINED here so run_bot.py's
    pass-through always parses; see the MERGE POINT below for where your
    existing filter wiring goes.

REQUIRES the updated live/trader.py (LiveTrader with the `sizer` parameter)
and sizing.py at the project root.
"""

import argparse
from live.trader import LiveTrader, RiskManager
from strategies.mean_reversion import MeanReversion
from strategies.momentum import Momentum
from strategies.pairs import PairsTrading

STRATEGY_CHOICES = ["meanrev", "mean_reversion", "momentum", "trend",
                    "pairs", "statarb", "breakout", "pyramid", "xsec"]


def build_strategy(name, symbols):
    if name in ("meanrev", "mean_reversion"):
        return MeanReversion(symbols)
    if name in ("momentum", "trend"):
        return Momentum(symbols)
    if name in ("pairs", "statarb"):
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


def infer_lookback(strat, floor=120) -> int:
    """How many bars of history the strategy needs. Reads .params if the
    strategy has one (the original strategies do) and falls back to common
    attribute names (the new ones). Recurses into wrappers via .inner."""
    vals = [0]
    params = getattr(strat, "params", None)
    if isinstance(params, dict):
        for k in ("lookback", "slow", "entry", "window"):
            try:
                vals.append(int(params.get(k, 0) or 0))
            except (TypeError, ValueError):
                pass
    for k in ("lookback", "slow", "entry", "window", "atr_window", "skip"):
        v = getattr(strat, k, 0)
        if isinstance(v, (int, float)):
            vals.append(int(v))
    inner = getattr(strat, "inner", None)   # e.g. RegimeFilter wrapping
    if inner is not None:
        vals.append(infer_lookback(inner, floor=0))
    return max(vals + [floor])


def build_broker(args):
    if args.broker == "alpaca":
        from live.alpaca_bot import AlpacaBroker
        return AlpacaBroker(paper=True)
    if args.broker == "ibkr":
        from live.ibkr_bot import IBKRBroker
        return IBKRBroker(port=args.ib_port, sec_type=args.ib_sectype,
                          exchange=args.ib_exchange)
    raise SystemExit(f"Unknown broker: {args.broker}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--broker", default="alpaca", choices=["alpaca", "ibkr"])
    p.add_argument("--symbols", nargs="+", required=True)
    p.add_argument("--strategy", default="meanrev", choices=STRATEGY_CHOICES)
    p.add_argument("--interval", default="1Min",
                   choices=["1Min", "5Min", "15Min", "1Hour", "1Day"])
    p.add_argument("--poll", type=int, default=60, help="seconds between checks")
    p.add_argument("--max-daily-loss", type=float, default=3.0,
                   help="halt + flatten if down this %% on the day")
    p.add_argument("--max-per-symbol", type=float, default=0.25,
                   help="max fraction of equity per symbol")
    # Sizing / regime:
    p.add_argument("--vol-target", type=float, default=None,
                   help="annualized vol target for position sizing, e.g. 0.15 "
                        "(absent = fixed-fraction sizing)")
    p.add_argument("--regime-filter", action="store_true",
                   help="wrap the strategy in the efficiency-ratio regime veto")
    # Catalyst filters (defined here so run_bot.py's pass-through parses;
    # wired below at the MERGE POINT):
    p.add_argument("--news-filter", action="store_true")
    p.add_argument("--event-blackout", action="store_true")
    p.add_argument("--earnings-blackout", action="store_true")
    p.add_argument("--sec-blackout", action="store_true")
    # IBKR-specific:
    p.add_argument("--ib-port", type=int, default=7497,  # asof:ibkr-paper-tws-port
                   help="7497 paper TWS / 4002 paper Gateway")
    p.add_argument("--ib-sectype", default="FUT", choices=["FUT", "STK"])
    p.add_argument("--ib-exchange", default="NYMEX",
                   help="e.g. NYMEX (crude), CME (ES), COMEX (gold)")
    args = p.parse_args()

    if args.strategy in ("pairs", "statarb") and len(args.symbols) != 2:
        raise SystemExit("Pairs trading needs exactly 2 symbols.")
    if args.strategy == "xsec" and len(args.symbols) < 3:
        raise SystemExit("Cross-sectional momentum needs 3+ symbols.")

    strat = build_strategy(args.strategy, args.symbols)

    # Regime filter: trend strategies veto chop, mean-rev strategies veto
    # trends. Applied BEFORE catalyst filters so vetoes stack outermost-last.
    if args.regime_filter:
        from strategies.regime import RegimeFilter
        mode = ("trend" if args.strategy in
                ("momentum", "trend", "breakout", "pyramid", "xsec")
                else "mean_reversion")
        strat = RegimeFilter(strat, mode=mode)

    # ======================= MERGE POINT ================================
    # Catalyst filters. If your run_paper.py already has the catalyst-filter
    # patch (news/event/earnings/SEC vetoes), REPLACE this block with your
    # existing wiring — the flags above feed it (args.news_filter,
    # args.event_blackout, args.earnings_blackout, args.sec_blackout).
    # The import below is a best guess at your patch's API and exists only
    # so a filter-enabled config fails LOUDLY instead of silently ignoring
    # the flags.
    if any([args.news_filter, args.event_blackout,
            args.earnings_blackout, args.sec_blackout]):
        try:
            from strategies.news_filter import wrap_with_filters
            strat = wrap_with_filters(strat,
                                      news=args.news_filter,
                                      events=args.event_blackout,
                                      earnings=args.earnings_blackout,
                                      sec=args.sec_blackout)
        except ImportError:
            raise SystemExit(
                "Catalyst filter flags were set, but this run_paper.py "
                "doesn't have your filter wiring merged in. Port your "
                "existing filter block into the MERGE POINT section.")
    # =====================================================================

    # Optional volatility-targeted sizing — same module the backtests use.
    sizer = None
    if args.vol_target:
        from sizing import VolatilityTargeting, periods_per_year
        sizer = VolatilityTargeting(
            target_annual_vol=args.vol_target,
            periods_per_year=periods_per_year(args.interval))
        print(f"Vol targeting ON: {args.vol_target:.0%} annualized.")

    broker = build_broker(args)
    risk = RiskManager(max_daily_loss_pct=args.max_daily_loss,
                       max_gross_per_symbol=args.max_per_symbol)
    lb = infer_lookback(strat)
    trader = LiveTrader(broker, strat, args.symbols, timeframe=args.interval,
                        lookback=lb + 10, risk=risk, poll_seconds=args.poll,
                        sizer=sizer)
    trader.run()


if __name__ == "__main__":
    main()
