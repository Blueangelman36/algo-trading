"""
Backtest the directional options strategy (model-based, Black-Scholes).

This prices options off the underlying's real price history and an estimated
volatility — it does NOT use real historical option quotes (those aren't
freely available). Read it as directional intuition about theta/delta and
your DTE/moneyness choices, not precise P&L. Not financial advice.

Examples:
  # Offline demo: momentum on a synthetic uptrend, expressed as calls/puts:
  python run_options_backtest.py --synthetic --strategy momentum

  # Mean reversion on the oil ETF as ~30-DTE ATM options, real history:
  python run_options_backtest.py --underlying USO --strategy meanrev --start 2020-01-01

  # Fixed 35% IV, 45-DTE, 5% OTM calls only, save equity curve:
  python run_options_backtest.py --underlying SPY --strategy momentum --start 2019-01-01 \
      --dte 45 --otm 0.05 --iv-mode fixed --iv 0.35 --no-puts --plot opt.png
"""

import argparse
from backtest.options_engine import OptionsBacktest
from strategies.momentum import Momentum
from strategies.mean_reversion import MeanReversion
from data import loader


def build_strategy(name, symbol):
    if name in ("meanrev", "mean_reversion"):
        return MeanReversion([symbol])
    if name in ("momentum", "trend"):
        return Momentum([symbol])
    raise SystemExit(f"Unsupported strategy for options backtest: {name}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--underlying", default=None)
    p.add_argument("--strategy", default="momentum")
    p.add_argument("--start", default="2020-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--dte", type=int, default=30)
    p.add_argument("--otm", type=float, default=0.0, help="fraction OTM (0.05 = 5%%)")
    p.add_argument("--qty", type=int, default=1, help="contracts per trade")
    p.add_argument("--min-dte-exit", type=int, default=5)
    p.add_argument("--no-puts", action="store_true", help="calls only")
    p.add_argument("--iv-mode", default="realized", choices=["realized", "fixed"])
    p.add_argument("--iv", type=float, default=0.30, help="fixed IV (iv-mode fixed)")
    p.add_argument("--iv-premium", type=float, default=1.1,
                   help="multiply realized vol to approximate IV")
    p.add_argument("--commission", type=float, default=0.65, help="per contract")
    p.add_argument("--spread-pct", type=float, default=0.01,
                   help="round-trip bid/ask haircut fraction")
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--plot", default=None)
    args = p.parse_args()

    if args.synthetic:
        data = loader.make_synthetic()
        underlying = "TREND"
        print(f"Using SYNTHETIC data; underlying={underlying}")
    else:
        if not args.underlying:
            raise SystemExit("Provide --underlying (or use --synthetic).")
        underlying = args.underlying
        print(f"Fetching {underlying} {args.start}..{args.end or 'today'}")
        data = loader.load_yf([underlying], args.start, args.end, "1d")
        if not data:
            raise SystemExit("No data fetched.")

    bt = OptionsBacktest(
        build_strategy(args.strategy, underlying), underlying, data,
        starting_cash=args.cash, target_dte=args.dte, otm_offset=args.otm,
        qty=args.qty, min_dte_exit=args.min_dte_exit, allow_puts=not args.no_puts,
        iv_mode=args.iv_mode, iv=args.iv, iv_premium=args.iv_premium,
        commission_per_contract=args.commission, spread_pct=args.spread_pct)
    bt.run()

    m = bt.metrics()
    ts = bt.trade_stats()
    print("\n" + "=" * 52)
    print(f" Options backtest (MODEL-BASED): {bt.strategy.name} on {underlying}")
    print("=" * 52)
    print(f"  Total return:    {m['total_return_pct']:+.2f}%")
    print(f"  CAGR:            {m['cagr_pct']:+.2f}%")
    print(f"  Sharpe:          {m['sharpe']}")
    print(f"  Max drawdown:    {m['max_drawdown_pct']:.2f}%")
    print("-" * 52)
    if ts.get("num_trades", 0):
        print(f"  Option trades:   {ts['num_trades']} "
              f"(calls {ts['calls']}, puts {ts['puts']})")
        print(f"  Win rate:        {ts['win_rate_pct']}%")
        print(f"  Profit factor:   {ts['profit_factor']}")
        print(f"  Avg win/loss:    ${ts['avg_win']:,.2f} / ${ts['avg_loss']:,.2f}")
        print(f"  Avg held:        {ts['avg_held_days']} days")
        print(f"  Exit reasons:    {ts['exit_reasons']}")
    else:
        print("  No option trades generated.")
    print("=" * 52)
    print("  NOTE: model-priced options, not real quotes. Directional")
    print("  intuition only — theta/delta dynamics, not precise P&L.")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        eq = bt.equity_series()
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(eq.index, eq.values, lw=1.4, color="#7c3aed")
        ax.set_title(f"Options backtest equity — {bt.strategy.name} on {underlying}")
        ax.set_ylabel("Equity ($)")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(args.plot, dpi=120)
        print(f"Saved plot -> {args.plot}")


if __name__ == "__main__":
    main()
