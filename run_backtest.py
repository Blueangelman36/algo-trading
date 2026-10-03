"""
Run a backtest.

Examples (from the algo-trading/ directory):

  # Offline smoke test on synthetic data (no internet needed):
  python run_backtest.py --synthetic

  # Mean reversion on an oil ETF, daily bars:
  python run_backtest.py --symbols USO --strategy meanrev --start 2022-01-01

  # Momentum on crude futures + S&P futures:
  python run_backtest.py --symbols CL=F ES=F --strategy momentum --start 2022-01-01

  # Plot the equity curve to a PNG:
  python run_backtest.py --symbols SPY --strategy momentum --start 2021-01-01 --plot equity.png
"""

import argparse
from backtest.engine import BacktestEngine
from backtest.portfolio import Portfolio
from backtest import metrics
from backtest import trades as trade_report
from strategies.mean_reversion import MeanReversion
from strategies.momentum import Momentum
from strategies.pairs import PairsTrading
from data import loader


def build_strategy(name, symbols):
    if name in ("meanrev", "mean_reversion"):
        return MeanReversion(symbols)
    if name in ("momentum", "trend"):
        return Momentum(symbols)
    if name in ("pairs", "statarb"):
        return PairsTrading(symbols)
    raise SystemExit(f"Unknown strategy: {name}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", default=["SPY"])
    p.add_argument("--strategy", default="meanrev")
    p.add_argument("--start", default="2022-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--interval", default="1d")
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--commission", type=float, default=0.005,
                   help="per share; use 0 for commission-free")
    p.add_argument("--slippage-bps", type=float, default=1.0)
    p.add_argument("--synthetic", action="store_true",
                   help="use generated data, no network")
    p.add_argument("--plot", default=None, help="path to save equity-curve PNG")
    p.add_argument("--trades", action="store_true",
                   help="print the per-trade table")
    p.add_argument("--trades-csv", default=None,
                   help="export every round-trip trade to this CSV path")
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
        print(f"Fetching {symbols} {args.start}..{args.end or 'today'} ({args.interval})")
        data = loader.load_yf(symbols, args.start, args.end, args.interval)
        if not data:
            raise SystemExit("No data fetched. Check symbols / connection.")

    strat = build_strategy(args.strategy, symbols)
    n = len(symbols)
    # Warm up long enough for the strategy's longest indicator window.
    lb = max(strat.params.get("lookback", 0), strat.params.get("slow", 0),
             strat.params.get("breakout", 0))
    warmup = max(50, lb + 5)
    pf = Portfolio(starting_cash=args.cash,
                   commission_per_share=args.commission,
                   slippage_bps=args.slippage_bps)
    engine = BacktestEngine(strat, data, portfolio=pf,
                            max_gross_per_symbol=1.0 / n, warmup=warmup)

    print(f"\nRunning {strat.name} on {symbols} ...")
    pf = engine.run()
    m = metrics.compute(pf)
    print("\n" + "=" * 48)
    print(f" Backtest result: {strat.name}")
    print("=" * 48)
    print(metrics.summary_text(m))
    print("=" * 48)

    # Trade-by-trade report: match fills into round-trip trades.
    last_prices = {s: float(df["close"].iloc[-1]) for s, df in data.items()}
    trs = trade_report.extract_trades(pf.fills, last_prices)
    stats = trade_report.trade_stats(trs)
    print("\n Trade-by-trade")
    print("-" * 48)
    print(trade_report.stats_summary_text(stats))
    print("-" * 48)

    if args.trades:
        print("\n" + trade_report.trades_table(trs, limit=15))
        print("  (* = position still open at end, marked to last price)")

    if args.trades_csv:
        n = trade_report.trades_to_csv(trs, args.trades_csv)
        print(f"\nExported {n} trades -> {args.trades_csv}")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        eq = pf.equity_series()
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(eq.index, eq.values, lw=1.4)
        ax.set_title(f"{strat.name} equity curve  ({', '.join(symbols)})")
        ax.set_ylabel("Equity ($)")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(args.plot, dpi=120)
        print(f"Saved plot -> {args.plot}")


if __name__ == "__main__":
    main()
