"""
Turn a flat stream of fills into discrete round-trip TRADES, then compute
trade-by-trade statistics (win rate, profit factor, expectancy, etc.).

Why this is non-trivial: the portfolio only logs fills (buy 100, sell 40,
sell 60, ...). To say "this trade won/lost" you have to group fills into
round trips. A round trip here is defined flat-to-flat: a trade opens when
position leaves zero and closes when it returns to zero. Two edge cases
are handled explicitly:

  - Scale-outs: closing a position in pieces is still ONE trade.
  - Flips: going from +long straight to -short splits into two trades
    (the long closes at the cross, a new short opens with the remainder).

An invariant worth knowing: the sum of net P&L across all trades (with any
position still open at the end marked to the last price) equals the total
change in equity. test reconciles exactly against the equity curve.
"""

from dataclasses import dataclass
import pandas as pd

EPS = 1e-9


@dataclass
class Trade:
    symbol: str
    direction: str          # 'long' or 'short'
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    units: float            # size of the trade (absolute units)
    entry_price: float      # average entry
    exit_price: float       # average exit
    gross_pnl: float        # before commission
    commission: float
    net_pnl: float          # after commission
    return_pct: float       # net_pnl / entry notional * 100
    holding: pd.Timedelta
    still_open: bool = False


def _close(symbol, legs, still_open=False) -> Trade:
    """Build a Trade from its component legs: list of
    (time, units, price, commission). Entry legs share the trade's
    opening sign; exit legs are the opposite sign."""
    direction = "long" if legs[0][1] > 0 else "short"
    open_sign = 1 if direction == "long" else -1

    entry_u = sum(abs(u) for _, u, _, _ in legs if (u > 0) == (open_sign > 0))
    exit_u = sum(abs(u) for _, u, _, _ in legs if (u > 0) != (open_sign > 0))
    entry_notional = sum(abs(u) * p for _, u, p, _ in legs if (u > 0) == (open_sign > 0))
    exit_notional = sum(abs(u) * p for _, u, p, _ in legs if (u > 0) != (open_sign > 0))

    avg_entry = entry_notional / entry_u if entry_u else 0.0
    avg_exit = exit_notional / exit_u if exit_u else 0.0

    # Cash-flow P&L: buying is negative cash, selling positive. For a
    # flat-to-flat trade this nets to the trading profit, direction-agnostic.
    gross = sum(-u * p for _, u, p, _ in legs)
    commission = sum(c for _, _, _, c in legs)
    net = gross - commission
    ret = (net / entry_notional * 100) if entry_notional else 0.0

    entry_time = legs[0][0]
    exit_time = legs[-1][0]
    return Trade(symbol, direction, entry_time, exit_time,
                 entry_u, avg_entry, avg_exit, gross, commission, net, ret,
                 exit_time - entry_time, still_open)


def extract_trades(fills, last_prices: dict | None = None) -> list[Trade]:
    """Group fills (from Portfolio.fills) into round-trip trades. If a
    position is still open at the end and last_prices has its symbol, the
    open trade is marked to that price and flagged still_open=True."""
    last_prices = last_prices or {}
    by_symbol: dict[str, list] = {}
    for f in fills:
        by_symbol.setdefault(f.symbol, []).append(f)

    trades: list[Trade] = []
    for symbol, fl in by_symbol.items():
        fl.sort(key=lambda f: f.timestamp)
        position = 0.0
        legs: list = []

        for f in fl:
            u, p, c, t = f.units, f.price, f.commission, f.timestamp
            if abs(position) < EPS:
                legs = [(t, u, p, c)]
                position = u
                continue

            new_pos = position + u
            crossed = (position > EPS and new_pos < -EPS) or \
                      (position < -EPS and new_pos > EPS)

            if crossed:
                # Split: part closes to zero, remainder opens opposite trade.
                closing_u = -position
                frac = abs(closing_u) / abs(u)
                legs.append((t, closing_u, p, c * frac))
                trades.append(_close(symbol, legs))
                remainder = u - closing_u
                legs = [(t, remainder, p, c * (1 - frac))]
                position = remainder
            else:
                legs.append((t, u, p, c))
                position = new_pos
                if abs(position) < EPS:
                    trades.append(_close(symbol, legs))
                    legs = []
                    position = 0.0

        # Position still open at the end: mark to last price if we have one.
        if abs(position) > EPS and legs:
            mark = last_prices.get(symbol)
            if mark is not None:
                legs.append((legs[-1][0], -position, mark, 0.0))
                trades.append(_close(symbol, legs, still_open=True))

    trades.sort(key=lambda tr: tr.entry_time)
    return trades


def trade_stats(trades: list[Trade]) -> dict:
    closed = trades  # open trade (if any) is marked-to-market, counts in
    n = len(closed)
    if n == 0:
        return {"num_trades": 0}

    wins = [t for t in closed if t.net_pnl > 0]
    losses = [t for t in closed if t.net_pnl < 0]
    gross_win = sum(t.net_pnl for t in wins)
    gross_loss = sum(t.net_pnl for t in losses)  # negative

    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    win_rate = len(wins) / n
    profit_factor = (gross_win / abs(gross_loss)) if gross_loss != 0 else float("inf")
    expectancy = sum(t.net_pnl for t in closed) / n
    avg_hold = sum((t.holding for t in closed), pd.Timedelta(0)) / n

    return {
        "num_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate * 100, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "win_loss_ratio": round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else None,
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "expectancy_per_trade": round(expectancy, 2),
        "largest_win": round(max((t.net_pnl for t in closed), default=0), 2),
        "largest_loss": round(min((t.net_pnl for t in closed), default=0), 2),
        "avg_return_pct": round(sum(t.return_pct for t in closed) / n, 2),
        "avg_holding": str(avg_hold).split(".")[0],
        "open_at_end": sum(1 for t in closed if t.still_open),
    }


def stats_summary_text(s: dict) -> str:
    if s.get("num_trades", 0) == 0:
        return "  No completed trades."
    pf = s["profit_factor"] if s["profit_factor"] is not None else "inf"
    wl = s["win_loss_ratio"] if s["win_loss_ratio"] is not None else "n/a"
    lines = [
        f"  Trades:            {s['num_trades']}  ({s['wins']}W / {s['losses']}L)",
        f"  Win rate:          {s['win_rate_pct']:.1f}%",
        f"  Profit factor:     {pf}",
        f"  Expectancy/trade:  ${s['expectancy_per_trade']:,.2f}",
        f"  Avg win:           ${s['avg_win']:,.2f}",
        f"  Avg loss:          ${s['avg_loss']:,.2f}",
        f"  Win/loss ratio:    {wl}",
        f"  Largest win:       ${s['largest_win']:,.2f}",
        f"  Largest loss:      ${s['largest_loss']:,.2f}",
        f"  Avg return/trade:  {s['avg_return_pct']:+.2f}%",
        f"  Avg holding:       {s['avg_holding']}",
    ]
    if s.get("open_at_end"):
        lines.append(f"  (incl. {s['open_at_end']} position open at end, marked to last price)")
    return "\n".join(lines)


def trades_table(trades: list[Trade], limit: int | None = 12) -> str:
    """A compact per-trade table for the console."""
    if not trades:
        return "  (no trades)"
    rows = trades[-limit:] if limit else trades
    head = f"  {'symbol':<8}{'dir':<6}{'entry':<17}{'exit':<17}{'units':>9}{'in':>9}{'out':>9}{'net P&L':>11}{'ret%':>8}"
    out = [head, "  " + "-" * (len(head) - 2)]
    for t in rows:
        flag = "*" if t.still_open else ""
        out.append(
            f"  {t.symbol:<8}{t.direction:<6}"
            f"{t.entry_time.strftime('%Y-%m-%d %H:%M'):<17}"
            f"{t.exit_time.strftime('%Y-%m-%d %H:%M'):<17}"
            f"{t.units:>9.2f}{t.entry_price:>9.2f}{t.exit_price:>9.2f}"
            f"{t.net_pnl:>10.2f}{flag:<1}{t.return_pct:>7.2f}%"
        )
    if limit and len(trades) > limit:
        out.insert(1, f"  (showing last {limit} of {len(trades)})")
    return "\n".join(out)


def trades_to_csv(trades: list[Trade], path: str):
    df = pd.DataFrame([{
        "symbol": t.symbol, "direction": t.direction,
        "entry_time": t.entry_time, "exit_time": t.exit_time,
        "units": round(t.units, 4), "entry_price": round(t.entry_price, 4),
        "exit_price": round(t.exit_price, 4), "gross_pnl": round(t.gross_pnl, 2),
        "commission": round(t.commission, 2), "net_pnl": round(t.net_pnl, 2),
        "return_pct": round(t.return_pct, 3), "holding": t.holding,
        "still_open": t.still_open,
    } for t in trades])
    df.to_csv(path, index=False)
    return len(df)
