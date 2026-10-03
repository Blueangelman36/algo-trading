"""
Directional options paper-trading bot (Alpaca).

Takes a directional signal from one of the existing strategies computed on
the UNDERLYING stock/ETF, and expresses it with long options:
    bullish signal  -> buy an (ATM) CALL
    bearish signal  -> buy an (ATM) PUT   (if --allow-puts)
    neutral / flip  -> close the open option
It holds at most one option at a time and auto-closes when the contract gets
too close to expiry (gamma/theta get nasty in the last week or two).

Defined risk: you're only ever LONG options, so the most you can lose on a
position is the premium paid.

Setup (paper): pip install alpaca-py; set ALPACA_API_KEY / ALPACA_SECRET_KEY;
enable options on the paper account.

Examples:
  # Momentum on SPY, expressed as ~30-DTE ATM calls/puts:
  python run_options.py --underlying SPY --strategy momentum --interval 15Min --allow-puts

  # Mean reversion on the oil ETF, calls only, ~45 DTE, 2 contracts:
  python run_options.py --underlying USO --strategy meanrev --dte 45 --qty 2

This is educational tooling, not financial advice. Options involve
significant risk, including total loss of premium.
"""

import argparse
from datetime import date
from live.alpaca_bot import AlpacaBroker
from live.alpaca_options import AlpacaOptions, occ_type, occ_underlying
from strategies.base import Context
from strategies.momentum import Momentum
from strategies.mean_reversion import MeanReversion


def build_strategy(name, symbol):
    if name in ("meanrev", "mean_reversion"):
        return MeanReversion([symbol])
    if name in ("momentum", "trend"):
        return Momentum([symbol])
    raise SystemExit(f"Unknown / unsupported strategy for options: {name}")


def signal_from_weight(w):
    if w is None:
        return "hold"
    if w > 0.5:
        return "bull"
    if w < -0.5:
        return "bear"
    return "flat"


def held_option(opts, underlying):
    """Return (symbol, 'call'/'put') for an open option on this underlying,
    or (None, None)."""
    for p in opts.option_positions():
        sym = p.symbol
        if occ_underlying(sym) == underlying and float(p.qty) != 0:
            return sym, occ_type(sym)
    return None, None


def dte_of(option_symbol) -> int:
    # OCC date is YYMMDD starting 6 chars into the tail.
    tail = option_symbol[-15:]
    y, m, d = 2000 + int(tail[0:2]), int(tail[2:4]), int(tail[4:6])
    return (date(y, m, d) - date.today()).days


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--underlying", required=True)
    p.add_argument("--strategy", default="momentum")
    p.add_argument("--interval", default="15Min",
                   choices=["1Min", "5Min", "15Min", "1Hour", "1Day"])
    p.add_argument("--dte", type=int, default=30, help="target days-to-expiry")
    p.add_argument("--otm", type=float, default=0.0,
                   help="fraction OTM (0.03 = ~3%% OTM); 0 = ATM")
    p.add_argument("--qty", type=int, default=1, help="contracts per trade")
    p.add_argument("--min-dte-exit", type=int, default=10,
                   help="close the option if it drops below this many DTE")
    p.add_argument("--allow-puts", action="store_true",
                   help="buy puts on bearish signals (else just go flat)")
    p.add_argument("--poll", type=int, default=60)
    args = p.parse_args()

    broker = AlpacaBroker(paper=True)      # underlying bars + account + clock
    opts = AlpacaOptions(paper=True)       # options actions
    strat = build_strategy(args.strategy, args.underlying)
    lb = max(strat.params.get("lookback", 0), strat.params.get("slow", 0), 60) + 10

    print(f"Options bot: {strat.name} on {args.underlying} -> "
          f"~{args.dte}DTE long options. Ctrl-C to stop.")
    import time
    try:
        while True:
            print(f"[{__import__('pandas').Timestamp.now():%H:%M:%S}] tick")
            try:
                _step(args, broker, opts, strat, lb)
            except Exception as e:
                print(f"  error (continuing): {e}")
            time.sleep(args.poll)
    except KeyboardInterrupt:
        print("\nStopped.")


def _step(args, broker, opts, strat, lb):
    if not broker.is_market_open():
        print("  market closed; waiting.")
        return
    u = args.underlying
    bars = broker.get_bars(u, lb, args.interval)
    if bars.empty:
        print("  no bars yet.")
        return
    acct = broker.get_account()
    ctx = Context(history={u: bars}, positions={u: 0.0},
                  cash=acct["cash"], equity=acct["equity"])
    sig = signal_from_weight(strat.on_bar(ctx).get(u))
    held_sym, held_kind = held_option(opts, u)
    print(f"  signal={sig}  holding={held_kind or 'none'}")

    # 1) Expiry management: close anything getting too close to expiry.
    if held_sym and dte_of(held_sym) <= args.min_dte_exit:
        print(f"  closing {held_sym}: {dte_of(held_sym)}DTE <= {args.min_dte_exit}")
        opts.sell_to_close(held_sym)
        held_sym, held_kind = None, None

    # 2) Act on the signal.
    if sig == "bull":
        if held_kind == "put":
            opts.sell_to_close(held_sym); held_kind = None
        if held_kind != "call":
            c = opts.select_atm(u, "call", args.dte, args.otm)
            q = opts.quote(c.symbol)
            print(f"  BUY call {c.symbol} strike {c.strike_price} "
                  f"exp {c.expiration_date} mid~{q['mid']}")
            opts.buy_to_open(c.symbol, args.qty)
    elif sig == "bear":
        if held_kind == "call":
            opts.sell_to_close(held_sym); held_kind = None
        if args.allow_puts and held_kind != "put":
            c = opts.select_atm(u, "put", args.dte, args.otm)
            q = opts.quote(c.symbol)
            print(f"  BUY put {c.symbol} strike {c.strike_price} "
                  f"exp {c.expiration_date} mid~{q['mid']}")
            opts.buy_to_open(c.symbol, args.qty)
        elif held_kind:
            opts.sell_to_close(held_sym)
    elif sig == "flat":
        if held_sym:
            print(f"  closing {held_sym} (flat signal)")
            opts.sell_to_close(held_sym)


if __name__ == "__main__":
    main()
