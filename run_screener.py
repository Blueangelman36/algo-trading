"""
Scan a basket of symbols for tradeable (cointegrated) pairs.

Examples:
  # Offline demo on synthetic data (one real pair hidden among noise):
  python run_screener.py --synthetic

  # Screen a basket of energy names for pairs (daily bars, ~3 years):
  python run_screener.py --symbols USO BNO XLE XOM CVX COP --start 2022-01-01

  # Save the full ranking to CSV:
  python run_screener.py --symbols USO BNO XLE XOM --start 2022-01-01 --csv pairs.csv

Feed the winners straight into a pairs backtest, e.g.:
  python run_backtest.py --symbols USO BNO --strategy pairs --start 2022-01-01 --trades
"""

import argparse
from research import screener
from data import loader


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", default=["SPY"])
    p.add_argument("--start", default="2022-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--interval", default="1d")
    p.add_argument("--max-pvalue", type=float, default=0.05)
    p.add_argument("--hl-min", type=float, default=1.0,
                   help="min acceptable half-life (bars)")
    p.add_argument("--hl-max", type=float, default=60.0,
                   help="max acceptable half-life (bars)")
    p.add_argument("--synthetic", action="store_true",
                   help="use a generated basket, no network")
    p.add_argument("--csv", default=None, help="save full ranking to CSV")
    args = p.parse_args()

    if args.synthetic:
        data = loader.make_basket()
        print(f"Using SYNTHETIC basket: {list(data.keys())}")
    else:
        if len(args.symbols) < 2:
            raise SystemExit("Need at least 2 symbols to form a pair.")
        print(f"Fetching {args.symbols} {args.start}..{args.end or 'today'}")
        data = loader.load_yf(args.symbols, args.start, args.end, args.interval)
        if len(data) < 2:
            raise SystemExit("Fetched fewer than 2 usable symbols.")

    df = screener.screen(data, max_pvalue=args.max_pvalue,
                         hl_min=args.hl_min, hl_max=args.hl_max)
    print("\n" + "=" * 70)
    print(" Cointegration screen (ranked by p-value; lower = stronger)")
    print("=" * 70)
    print(screener.summary_text(df, max_pvalue=args.max_pvalue))

    if args.csv and not df.empty:
        df.drop(columns=["a", "b"]).to_csv(args.csv, index=False)
        print(f"\nSaved full ranking -> {args.csv}")


if __name__ == "__main__":
    main()
