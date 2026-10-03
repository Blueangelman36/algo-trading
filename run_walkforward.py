"""
Walk-forward analysis: optimize parameters in-sample, judge them out-of-sample.

Examples:
  # Offline demo on synthetic data:
  python run_walkforward.py --synthetic --strategy meanrev --train 250 --test 60

  # Momentum on SPY, rolling 1y train / 3m test, save the OOS curve:
  python run_walkforward.py --symbols SPY --strategy momentum --start 2018-01-01 \
      --train 252 --test 63 --plot wf.png

  # Anchored (growing) train window, optimizing total return:
  python run_walkforward.py --symbols USO --strategy meanrev --start 2018-01-01 \
      --train 252 --test 63 --anchored --metric return
"""

import argparse
from backtest import walkforward as wf
from data import loader


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", default=["SPY"])
    p.add_argument("--strategy", default="meanrev")
    p.add_argument("--start", default="2018-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--interval", default="1d")
    p.add_argument("--train", type=int, default=252, help="train window in bars")
    p.add_argument("--test", type=int, default=63, help="test window in bars")
    p.add_argument("--metric", default="sharpe", choices=["sharpe", "return", "sortino"])
    p.add_argument("--anchored", action="store_true", help="growing train window")
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--plot", default=None, help="save stitched OOS equity PNG")
    args = p.parse_args()

    if args.synthetic:
        if args.strategy in ("pairs", "statarb"):
            data = loader.make_cointegrated_pair()
        else:
            data = loader.make_synthetic()
        symbols = list(data.keys())
        print(f"Using SYNTHETIC data: {symbols}")
    else:
        symbols = args.symbols
        print(f"Fetching {symbols} {args.start}..{args.end or 'today'}")
        data = loader.load_yf(symbols, args.start, args.end, args.interval)
        if not data:
            raise SystemExit("No data fetched.")

    result = wf.walk_forward(data, symbols, args.strategy,
                             train_bars=args.train, test_bars=args.test,
                             metric=args.metric, anchored=args.anchored,
                             starting_cash=args.cash)
    print("\n" + "=" * 72)
    print(wf.summary_text(result))
    print("=" * 72)

    if args.plot and len(result["oos_equity"]) > 2:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        eq = result["oos_equity"]
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(eq.index, eq.values, lw=1.4, color="#0d9488")
        ax.set_title(f"Walk-forward OOS equity — {args.strategy} on {', '.join(symbols)}")
        ax.set_ylabel("Equity ($)")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(args.plot, dpi=120)
        print(f"Saved plot -> {args.plot}")


if __name__ == "__main__":
    main()
