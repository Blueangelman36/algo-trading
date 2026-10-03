"""
Research demo: this repository's backtester, pair screener, walk-forward
analysis and options model, in a browser.

Research only. Nothing here connects to a broker, needs a key, or places an
order. Every number comes from the same modules the command-line tools use.

Run locally (from the repository root):
    pip install -r demo/requirements.txt
    streamlit run demo/streamlit_app.py
"""

import inspect
import re
import sys
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest import metrics as M                      # noqa: E402
from backtest import trades as T                       # noqa: E402
from backtest import walkforward as WF                 # noqa: E402
from backtest.engine import BacktestEngine             # noqa: E402
from backtest.options_engine import OptionsBacktest    # noqa: E402
from backtest.portfolio import Portfolio               # noqa: E402
from data import loader                                # noqa: E402
from research import screener                          # noqa: E402
from strategies.breakout import Breakout               # noqa: E402
from strategies.mean_reversion import MeanReversion    # noqa: E402
from strategies.momentum import Momentum               # noqa: E402
from strategies.pairs import PairsTrading              # noqa: E402
from strategies.pyramiding_breakout import PyramidingBreakout  # noqa: E402
from strategies.xsec_momentum import CrossSectionalMomentum    # noqa: E402

REPO_URL = "https://github.com/Blueangelman36/algo-trading"

STRATEGIES = {
    "meanrev": ("Mean reversion", MeanReversion),
    "momentum": ("Momentum (moving-average crossover)", Momentum),
    "breakout": ("Breakout (Donchian channel)", Breakout),
    "pyramid": ("Pyramiding breakout (Turtle-style)", PyramidingBreakout),
    "pairs": ("Pairs trading (exactly 2 symbols)", PairsTrading),
    "xsec": ("Cross-sectional momentum (3+ symbols)", CrossSectionalMomentum),
}
# Walk-forward needs a parameter grid to search; these are the strategies
# backtest/walkforward.py has one for.
WF_STRATEGIES = [k for k in STRATEGIES if k in WF.DEFAULT_GRIDS]

# Keeps a free shared server responsive: ~12 years of daily bars, and a
# walk-forward of at most this many train/test folds.
MAX_BARS = 3000
MAX_FOLDS = 40
MAX_SYMBOLS = 10
SYMBOL_RE = re.compile(r"^[A-Za-z0-9.\-=^]{1,12}$")

# Chart colors: categorical slots 1-2 of the reference palette, stepped per
# mode (validated as a pair in both), plus muted ink for a benchmark line.
PALETTE = {
    "light": {"s1": "#2a78d6", "s2": "#eb6834", "muted": "#898781", "ink": "#52514e"},
    "dark": {"s1": "#3987e5", "s2": "#d95926", "muted": "#898781", "ink": "#c3c2b7"},
}

st.set_page_config(page_title="algo-trading research demo", page_icon="📈",
                   layout="wide")


# ---- helpers ---------------------------------------------------------------

def colors() -> dict:
    try:
        mode = "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        mode = "light"
    return PALETTE[mode]


def parse_symbols(text: str) -> list[str]:
    out = []
    for raw in re.split(r"[\s,]+", text.strip()):
        sym = raw.upper()
        if not sym:
            continue
        if not SYMBOL_RE.match(sym):
            raise ValueError(f"'{raw}' doesn't look like a ticker symbol.")
        if sym not in out:
            out.append(sym)
    if len(out) > MAX_SYMBOLS:
        raise ValueError(f"Use at most {MAX_SYMBOLS} symbols.")
    return out


@st.cache_data(ttl=3600, max_entries=64, show_spinner=False)
def fetch_yahoo(symbols: tuple, start: str, end: str) -> dict:
    data = loader.load_yf(list(symbols), start, end)
    return {s: df.tail(MAX_BARS) for s, df in data.items()}


def load_data(symbols: list[str], synthetic) -> dict:
    """Synthetic data when that source is selected, else Yahoo Finance."""
    if st.session_state.source == "synthetic":
        return synthetic()
    try:
        data = fetch_yahoo(tuple(symbols), str(st.session_state.start),
                           str(st.session_state.end))
    except Exception as e:
        raise RuntimeError(
            f"Yahoo Finance didn't return data ({e}). It often rate-limits "
            "shared cloud servers; wait a minute and try again, or switch "
            "the sidebar to synthetic data.") from None
    missing = [s for s in symbols if s not in data]
    if missing:
        raise RuntimeError(f"No data for {', '.join(missing)}. Check the symbols "
                           "and the date range.")
    return data


def default_params(cls) -> dict:
    sig = inspect.signature(cls.__init__)
    return {n: p.default for n, p in sig.parameters.items()
            if n not in ("self", "symbols") and p.default is not inspect.Parameter.empty}


def param_inputs(params: dict, key: str) -> dict:
    """One input per constructor parameter, typed by its default."""
    out = {}
    cols = st.columns(4)
    for i, (name, default) in enumerate(params.items()):
        label = name.replace("_", " ")
        with cols[i % 4]:
            k = f"{key}_{name}"
            if isinstance(default, bool):
                out[name] = st.checkbox(label, value=default, key=k)
            elif isinstance(default, int):
                out[name] = int(st.number_input(label, value=default, step=1, key=k,
                                                min_value=0 if default == 0 else 1))
            elif isinstance(default, float):
                out[name] = float(st.number_input(label, value=default, step=0.1,
                                                  format="%.2f", key=k, min_value=0.0))
    return out


def symbol_rule(key: str, symbols: list[str]):
    if key == "pairs" and len(symbols) != 2:
        raise ValueError("Pairs trading needs exactly 2 symbols.")
    if key == "xsec" and len(symbols) < 3:
        raise ValueError("Cross-sectional momentum needs at least 3 symbols.")


def synthetic_for(key: str):
    if key == "pairs":
        return loader.make_cointegrated_pair
    if key == "xsec":
        return loader.make_basket
    return loader.make_synthetic


def pct(x, signed=True) -> str:
    if x is None or (isinstance(x, float) and x != x):
        return "n/a"
    return f"{x:+.1f}%" if signed else f"{x:.1f}%"


def num(x) -> str:
    return "n/a" if x is None else f"{x:.2f}"


def tiles(items: list[tuple[str, str, str]]):
    """A row of stat tiles: (label, value, help)."""
    cols = st.columns(len(items))
    for col, (label, value, help_) in zip(cols, items):
        col.metric(label, value, help=help_ or None)


def series_chart(series: dict, palette: dict, y_title: str, fmt: str,
                 area: bool = False, height: int = 300, margin: bool = False):
    """Line (or area) chart with a hover crosshair and tooltip. Two or more
    series get a legend and a label at each line's end, in a right margin;
    `margin` reserves the same space so stacked charts' dates line up."""
    names = list(series)
    df = pd.concat([s.rename("value").rename_axis("date").reset_index().assign(series=n)
                    for n, s in series.items()], ignore_index=True)
    color = alt.Color("series:N", legend=(alt.Legend(orient="top", title=None)
                                          if len(names) > 1 else None),
                      scale=alt.Scale(domain=names, range=[palette[n] for n in names]))
    base = alt.Chart(df).encode(x=alt.X("date:T", title=None,
                                        axis=alt.Axis(format="%b %Y")))
    y = alt.Y("value:Q", title=y_title, axis=alt.Axis(format=fmt, minExtent=64),
              scale=alt.Scale(zero=area))
    if area:
        mark = base.mark_area(opacity=0.22, line={"strokeWidth": 2})
    else:
        mark = base.mark_line(strokeWidth=2)
    hover = alt.selection_point(fields=["date"], nearest=True, on="pointerover",
                                empty=False, clear="pointerout")
    points = base.mark_circle(size=64).encode(
        y=y, color=color, opacity=alt.condition(hover, alt.value(1), alt.value(0)))
    tooltip = [alt.Tooltip("date:T", title="Date")] + [
        alt.Tooltip(f"{n}:Q", title=n, format=fmt) for n in names]
    rule = (base.transform_pivot("series", value="value", groupby=["date"])
            .mark_rule(color=colors()["muted"])
            .encode(opacity=alt.condition(hover, alt.value(0.6), alt.value(0)),
                    tooltip=tooltip)
            .add_params(hover))
    layers = [mark.encode(y=y, color=color), points, rule]
    pad = {"left": 5, "top": 5, "bottom": 5, "right": 95}
    chart = alt.layer(*layers).properties(height=height)
    if margin:
        chart = chart.properties(padding=pad)
    if len(names) > 1:
        # Label each line just past its last point, in the right margin.
        ends = df.loc[df.groupby("series")["date"].idxmax()]
        chart = alt.layer(*layers, alt.Chart(ends).mark_text(
            align="left", dx=8, color=colors()["ink"]).encode(
            x="date:T", y="value:Q", text="series:N")).properties(
            height=height, padding=pad)
    return chart


def show_chart(chart):
    st.altair_chart(chart, width="stretch")


# ---- sidebar ---------------------------------------------------------------

with st.sidebar:
    st.header("Data")
    st.radio("Source", ["synthetic", "yahoo"], key="source",
             format_func=lambda s: {"synthetic": "Synthetic (offline, instant)",
                                    "yahoo": "Yahoo Finance (real prices)"}[s])
    yahoo = st.session_state.source == "yahoo"
    st.date_input("Start", value=date(2020, 1, 1), key="start", disabled=not yahoo,
                  min_value=date(2000, 1, 1), max_value=date.today())
    st.date_input("End", value=date.today(), key="end", disabled=not yahoo,
                  min_value=date(2000, 1, 2), max_value=date.today())
    if yahoo:
        st.caption(f"Daily bars, at most the last {MAX_BARS:,} per symbol. Yahoo "
                   "sometimes rate-limits shared servers; if a fetch fails, "
                   "wait a minute.")
    else:
        st.caption("Generated series with known behavior, so you can see each "
                   "tool work without a network: a trending and a mean-reverting "
                   "series, a cointegrated pair, or a basket with one real pair "
                   "hidden among random walks.")

    st.header("Costs")
    st.number_input("Starting cash ($)", value=100_000.0, min_value=1_000.0,
                    step=10_000.0, key="cash", format="%.0f")
    st.number_input("Commission ($ per share)", value=0.005, min_value=0.0,
                    step=0.001, format="%.3f", key="commission",
                    help="Set 0 to see how much of an edge is just ignoring costs.")
    st.number_input("Slippage (bps)", value=1.0, min_value=0.0, step=0.5,
                    key="slippage")


# ---- page --------------------------------------------------------------------

st.title("algo-trading research demo")
st.markdown(
    f"The research half of [algo-trading]({REPO_URL}) in a browser: backtest a "
    "strategy, screen for cointegrated pairs, check for overfitting with a "
    "walk-forward test, and model an options version. **Research only.** "
    "Nothing here connects to a broker or places an order, and none of it "
    "is financial advice.")

tab_bt, tab_scr, tab_wf, tab_opt = st.tabs(
    ["Backtest", "Pair screener", "Walk-forward", "Options (model)"])


# ---- Backtest ----------------------------------------------------------------

with tab_bt:
    key = st.selectbox("Strategy", list(STRATEGIES), key="bt_strategy",
                       format_func=lambda k: STRATEGIES[k][0])
    cls = STRATEGIES[key][1]
    st.caption((inspect.getdoc(inspect.getmodule(cls)) or "").split("\n\n")[0])
    with st.form("bt_form"):
        default_syms = {"pairs": "USO BNO", "xsec": "XLE XOM CVX COP OXY"}.get(key, "USO")
        syms_text = st.text_input("Symbols", value=default_syms, disabled=not yahoo,
                                  key=f"bt_symbols_{key}",
                                  help="Space- or comma-separated tickers.")
        params = param_inputs(default_params(cls), f"bt_{key}")
        next_open = st.checkbox(
            "Fill at the next bar's open", value=False, key="bt_next_open",
            help="The default fills at the signal bar's close, which is "
                 "optimistic: live, you can't act on a close until it has "
                 "printed. If an edge survives next-open fills it is more "
                 "likely real.")
        run = st.form_submit_button("Run backtest", type="primary", key="bt_run")

    if run:
        try:
            symbols = parse_symbols(syms_text) if yahoo else []
            if yahoo:
                symbol_rule(key, symbols)
            with st.spinner("Backtesting..."):
                data = load_data(symbols, synthetic_for(key))
                symbols = list(data)
                strat = cls(symbols, **params)
                pf = Portfolio(starting_cash=st.session_state.cash,
                               commission_per_share=st.session_state.commission,
                               slippage_bps=st.session_state.slippage)
                BacktestEngine(strat, data, portfolio=pf,
                               max_gross_per_symbol=1.0 / len(symbols),
                               warmup=WF._warmup_for(params),
                               fill_mode="next_open" if next_open else "close").run()
                m = M.compute(pf)
                if "error" in m:
                    raise RuntimeError("Not enough bars to backtest; widen the date range.")
                last = {s: float(df["close"].iloc[-1]) for s, df in data.items()}
                trs = T.extract_trades(pf.fills, last)
                eq = pf.equity_series()
                closes = pd.DataFrame({s: df["close"] for s, df in data.items()})
                closes = closes.reindex(eq.index).ffill().bfill()
                hold = (closes / closes.iloc[0]).mean(axis=1) * st.session_state.cash
            st.session_state.bt_result = {
                "name": STRATEGIES[key][0], "symbols": symbols, "metrics": m,
                "stats": T.trade_stats(trs), "equity": eq, "hold": hold,
                "trades": trs}
        except Exception as e:
            st.session_state.pop("bt_result", None)
            st.error(str(e))

    r = st.session_state.get("bt_result")
    if r:
        m, s = r["metrics"], r["stats"]
        st.subheader(f"{r['name']} on {', '.join(r['symbols'])}")
        tiles([
            ("Total return", pct(m.get("total_return_pct")), ""),
            ("CAGR", pct(m.get("cagr_pct")), ""),
            ("Sharpe", num(m.get("sharpe")),
             "Below ~1 is weak. Above ~2-3 in a backtest usually means overfitting."),
            ("Max drawdown", pct(-abs(m.get("max_drawdown_pct") or 0)),
             "What blows up accounts. Weigh it above headline return."),
        ])
        tiles([
            ("Trades", f"{s.get('num_trades', 0)}",
             "A great result from few trades is noise, not edge."),
            ("Win rate", pct(s.get("win_rate_pct"), signed=False),
             "A trap on its own: read it with profit factor and expectancy."),
            ("Profit factor", num(s.get("profit_factor")),
             "Gross wins / gross losses. Above 1 is profitable; ~1.5+ is healthy."),
            ("Expectancy / trade", f"${s['expectancy_per_trade']:,.2f}"
             if "expectancy_per_trade" in s else "n/a",
             "Average dollars per trade after costs. The most honest number."),
        ])
        pal = colors()
        show_chart(series_chart(
            {"Strategy": r["equity"], "Buy and hold": r["hold"]},
            {"Strategy": pal["s1"], "Buy and hold": pal["muted"]},
            "Equity ($)", "$,.0f"))
        st.caption("Buy and hold = the same symbols held in equal weight over the "
                   "same span, for scale.")
        eq = r["equity"]
        show_chart(series_chart({"Drawdown": eq / eq.cummax() - 1},
                                {"Drawdown": pal["s1"]}, "Drawdown", ".0%",
                                area=True, height=160, margin=True))
        trades_df = pd.DataFrame([{
            "symbol": t.symbol, "direction": t.direction,
            "entry": t.entry_time, "exit": t.exit_time,
            "units": round(t.units, 2), "entry price": round(t.entry_price, 2),
            "exit price": round(t.exit_price, 2), "net P&L": round(t.net_pnl, 2),
            "return %": round(t.return_pct, 2),
            "held": str(t.holding).split(".")[0], "open at end": t.still_open,
        } for t in r["trades"]])
        with st.expander(f"Every trade ({len(trades_df)})"):
            st.dataframe(trades_df, width="stretch", hide_index=True)
            if len(trades_df):
                st.download_button("Download CSV", trades_df.to_csv(index=False),
                                   "trades.csv", "text/csv", key="bt_csv")
        with st.expander("Equity curve as a table"):
            st.dataframe(pd.DataFrame({"strategy": eq.round(2),
                                       "buy and hold": r["hold"].round(2)}),
                         width="stretch")


# ---- Pair screener -------------------------------------------------------------

with tab_scr:
    st.caption("Tests every pair in a basket for cointegration (Engle-Granger, "
               "ADF on the spread) and ranks them. Pairs trading only works on "
               "a pair whose spread genuinely mean-reverts.")
    with st.form("scr_form"):
        syms_text = st.text_input("Basket", value="USO BNO XLE XOM CVX COP",
                                  disabled=not yahoo, key="scr_symbols")
        c1, c2, c3 = st.columns(3)
        max_p = c1.number_input("Max p-value", value=0.05, min_value=0.001,
                                max_value=0.5, step=0.01, format="%.3f", key="scr_p")
        hl_min = c2.number_input("Min half-life (bars)", value=1.0, min_value=0.0,
                                 step=1.0, key="scr_hlmin")
        hl_max = c3.number_input("Max half-life (bars)", value=60.0, min_value=1.0,
                                 step=5.0, key="scr_hlmax")
        run = st.form_submit_button("Screen pairs", type="primary", key="scr_run")

    if run:
        try:
            symbols = parse_symbols(syms_text) if yahoo else []
            if yahoo and len(symbols) < 2:
                raise ValueError("Need at least 2 symbols to form a pair.")
            with st.spinner("Testing pairs..."):
                data = load_data(symbols, loader.make_basket)
                df = screener.screen(data, max_pvalue=max_p, hl_min=hl_min,
                                     hl_max=hl_max)
            if df.empty:
                raise RuntimeError("No pair had enough overlapping history to test.")
            st.session_state.scr_result = {"df": df, "max_p": max_p}
        except Exception as e:
            st.session_state.pop("scr_result", None)
            st.error(str(e))

    r = st.session_state.get("scr_result")
    if r:
        df = r["df"]
        n_ok = int(df["tradeable"].sum())
        tiles([("Pairs tested", f"{len(df)}", ""),
               ("Tradeable", f"{n_ok}",
                "Cointegration p-value under the cutoff AND half-life inside the band."),
               ("Expected by chance", f"~{len(df) * r['max_p']:.1f}",
                "Test enough pairs and some pass at random. Verify survivors "
                "out of sample before trusting them.")])
        st.dataframe(
            df.drop(columns=["a", "b"]), width="stretch", hide_index=True,
            column_config={
                "coint_p": st.column_config.NumberColumn("coint p", format="%.4f"),
                "adf_p": st.column_config.NumberColumn("ADF p", format="%.4f"),
                "half_life": st.column_config.NumberColumn(
                    "half-life", help="Bars for the spread to close half its gap."),
                "hedge_drift": st.column_config.NumberColumn(
                    "hedge drift", help="How much a rolling hedge ratio wanders. "
                    "Large = the pair quietly de-hedges live."),
                "current_z": st.column_config.NumberColumn("spread z now"),
                "tradeable": st.column_config.CheckboxColumn("tradeable"),
            })
        if n_ok:
            top = df[df["tradeable"]].iloc[0]
            st.info(f"Next step: backtest **{top['a']} / {top['b']}** with *Pairs "
                    "trading* on the Backtest tab, then put it through the "
                    "walk-forward test.")


# ---- Walk-forward ----------------------------------------------------------------

with tab_wf:
    st.caption("Optimizes parameters on a train window, scores them on the next "
               "unseen window, and repeats. The stitched out-of-sample curve is "
               "the honest result; the gap between in-sample and out-of-sample "
               "is how much of the backtest was curve-fitting.")
    key = st.selectbox("Strategy", WF_STRATEGIES, key="wf_strategy",
                       format_func=lambda k: STRATEGIES[k][0])
    grid = WF.DEFAULT_GRIDS[key]
    st.caption("Searches " + ", ".join(f"{k} in {v}" for k, v in grid.items()))
    with st.form("wf_form"):
        default_syms = {"pairs": "USO BNO", "xsec": "XLE XOM CVX COP OXY"}.get(key, "SPY")
        syms_text = st.text_input("Symbols", value=default_syms, disabled=not yahoo,
                                  key=f"wf_symbols_{key}")
        c1, c2, c3, c4 = st.columns(4)
        train = c1.number_input("Train window (bars)", value=250, min_value=60,
                                step=21, key="wf_train")
        test = c2.number_input("Test window (bars)", value=60, min_value=10,
                               step=21, key="wf_test")
        metric = c3.selectbox("Optimize for", ["sharpe", "return", "sortino"],
                              key="wf_metric")
        anchored = c4.checkbox("Anchored", key="wf_anchored",
                               help="Grow the train window from a fixed start "
                                    "instead of sliding it forward.")
        run = st.form_submit_button("Run walk-forward", type="primary", key="wf_run")

    if run:
        try:
            symbols = parse_symbols(syms_text) if yahoo else []
            if yahoo:
                symbol_rule(key, symbols)
            data = load_data(symbols, synthetic_for(key))
            n_bars = max(len(df) for df in data.values())
            folds = (n_bars - int(train)) // int(test)
            if folds < 1:
                raise ValueError(f"{n_bars} bars can't fit a {train}-bar train plus "
                                 f"a {test}-bar test window. Shorten them or widen "
                                 "the date range.")
            if folds > MAX_FOLDS:
                raise ValueError(f"That's {folds} folds; this demo stops at "
                                 f"{MAX_FOLDS}. Use a longer test window or a later "
                                 "start date.")
            with st.spinner(f"Optimizing and testing {folds} folds..."):
                res = WF.walk_forward(data, list(data), key, train_bars=int(train),
                                      test_bars=int(test), metric=metric,
                                      anchored=anchored,
                                      starting_cash=st.session_state.cash,
                                      commission=st.session_state.commission,
                                      slippage_bps=st.session_state.slippage)
            if len(res["oos_equity"]) < 3:
                raise RuntimeError("The out-of-sample curve came back empty; "
                                   "try more history or shorter windows.")
            st.session_state.wf_result = {**res, "symbols": list(data), "key": key}
        except Exception as e:
            st.session_state.pop("wf_result", None)
            st.error(str(e))

    r = st.session_state.get("wf_result")
    if r:
        om = r["oos_metrics"]
        folds = r["folds"]
        is_scores = [f["is_score"] for f in folds if f["is_score"] is not None]
        oos_sharpes = [f["oos_sharpe"] for f in folds if f["oos_sharpe"] is not None]
        st.subheader(f"{STRATEGIES[r['key']][0]} on {', '.join(r['symbols'])}")
        tiles([
            ("Out-of-sample return", pct(om.get("total_return_pct")), ""),
            ("Out-of-sample Sharpe", num(om.get("sharpe")), ""),
            ("Out-of-sample max drawdown", pct(-abs(om.get("max_drawdown_pct") or 0)), ""),
            ("Folds", f"{len(folds)}", ""),
        ])
        pal = colors()
        show_chart(series_chart({"Out-of-sample equity": r["oos_equity"]},
                                {"Out-of-sample equity": pal["s1"]},
                                "Equity ($)", "$,.0f"))
        if r["metric"] == "sharpe" and is_scores and oos_sharpes:
            st.markdown("**In-sample vs out-of-sample Sharpe, per fold.** If the "
                        "blue bars tower over the orange ones, the optimizer was "
                        "fitting noise. A missing bar means no trades in that "
                        "window, so no Sharpe.")
            rows = []
            for f in folds:
                for label, v in (("In-sample", f["is_score"]),
                                 ("Out-of-sample", f["oos_sharpe"])):
                    if v is not None:
                        rows.append({"fold": f["fold"], "window": label, "sharpe": v,
                                     "test": f["test"]})
            bars = alt.Chart(pd.DataFrame(rows)).mark_bar(cornerRadiusEnd=4).encode(
                x=alt.X("fold:O", title="Fold", axis=alt.Axis(labelAngle=0),
                        scale=alt.Scale(paddingInner=0.25)),
                xOffset=alt.XOffset("window:N", sort=["In-sample", "Out-of-sample"],
                                    scale=alt.Scale(paddingInner=0.08)),
                y=alt.Y("sharpe:Q", title="Sharpe"),
                color=alt.Color("window:N", legend=alt.Legend(orient="top", title=None),
                                scale=alt.Scale(domain=["In-sample", "Out-of-sample"],
                                                range=[pal["s1"], pal["s2"]])),
                tooltip=["fold:O", "test:N", "window:N",
                         alt.Tooltip("sharpe:Q", format=".2f")])
            zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
                color=pal["muted"]).encode(y="y:Q")
            show_chart((bars + zero).properties(height=240))
        stab = r["param_stability"]
        if stab:
            st.markdown("**Parameter stability.** How often the optimizer picked "
                        "the same value. Under 50% means the 'best' value is noise, "
                        "even if the out-of-sample curve looks fine.")
            st.dataframe(pd.DataFrame([
                {"parameter": k, "most picked": str(v["mode"]),
                 "share of folds": f"{v['share']:.0%}",
                 "all picks": ", ".join(f"{a}: {b}" for a, b in v["counts"].items())}
                for k, v in stab.items()]), width="stretch", hide_index=True)
        with st.expander("Every fold"):
            st.dataframe(pd.DataFrame([{**f, "best_params": str(f["best_params"])}
                                       for f in folds]),
                         width="stretch", hide_index=True)


# ---- Options -----------------------------------------------------------------------

with tab_opt:
    st.caption("Takes a directional signal on the underlying and buys an option to "
               "express it, so the most a position can lose is its premium. Options "
               "are priced with Black-Scholes from the underlying's real prices and "
               "an estimated volatility, not historical option quotes: read it for "
               "theta and delta intuition and for choosing expiry and strike, not "
               "for precise P&L.")
    with st.form("opt_form"):
        c1, c2 = st.columns(2)
        underlying = c1.text_input("Underlying", value="USO", disabled=not yahoo,
                                   key="opt_symbol")
        okey = c2.selectbox("Signal", ["momentum", "meanrev"], key="opt_strategy",
                            format_func=lambda k: STRATEGIES[k][0])
        c1, c2, c3, c4 = st.columns(4)
        dte = c1.number_input("Days to expiry at entry", value=30, min_value=7,
                              max_value=365, step=5, key="opt_dte")
        otm = c2.number_input("Out of the money (%)", value=0.0, min_value=0.0,
                              max_value=30.0, step=1.0, key="opt_otm")
        qty = c3.number_input("Contracts per trade", value=1, min_value=1,
                              max_value=50, step=1, key="opt_qty")
        puts = c4.checkbox("Allow puts", value=True, key="opt_puts",
                           help="Off = calls only, so bearish signals stay flat.")
        run = st.form_submit_button("Run options backtest", type="primary",
                                    key="opt_run")

    if run:
        try:
            symbols = parse_symbols(underlying) if yahoo else []
            if yahoo and len(symbols) != 1:
                raise ValueError("Enter exactly one underlying symbol.")
            with st.spinner("Pricing options..."):
                data = load_data(symbols, loader.make_synthetic)
                sym = symbols[0] if yahoo else "TREND"
                bt = OptionsBacktest(
                    STRATEGIES[okey][1]([sym]), sym, data,
                    starting_cash=st.session_state.cash, target_dte=int(dte),
                    otm_offset=otm / 100, qty=int(qty), allow_puts=puts,
                    iv_premium=1.1)
                bt.run()
                m = bt.metrics()
            if "error" in m:
                raise RuntimeError("Not enough bars to backtest; widen the date range.")
            st.session_state.opt_result = {"sym": sym, "key": okey, "metrics": m,
                                           "stats": bt.trade_stats(),
                                           "equity": bt.equity_series(),
                                           "trades": bt.trades}
        except Exception as e:
            st.session_state.pop("opt_result", None)
            st.error(str(e))

    r = st.session_state.get("opt_result")
    if r:
        m, s = r["metrics"], r["stats"]
        st.subheader(f"{STRATEGIES[r['key']][0]} signal, options on {r['sym']}")
        tiles([
            ("Total return", pct(m.get("total_return_pct")), ""),
            ("Sharpe", num(m.get("sharpe")), ""),
            ("Max drawdown", pct(-abs(m.get("max_drawdown_pct") or 0)), ""),
            ("Option trades", f"{s.get('num_trades', 0)}"
             + (f" ({s['calls']} calls, {s['puts']} puts)" if s.get("num_trades") else ""), ""),
            ("Win rate", pct(s.get("win_rate_pct"), signed=False), ""),
        ])
        show_chart(series_chart({"Equity": r["equity"]}, {"Equity": colors()["s1"]},
                                "Equity ($)", "$,.0f"))
        if r["trades"]:
            with st.expander(f"Every option trade ({len(r['trades'])})"):
                st.dataframe(pd.DataFrame([{
                    "type": t.kind, "strike": round(t.strike, 2), "contracts": t.qty,
                    "entry": t.entry_date, "exit": t.exit_date,
                    "entry premium": round(t.entry_price, 2),
                    "exit premium": round(t.exit_price, 2),
                    "net P&L": round(t.pnl, 2), "exit reason": t.reason,
                    "days held": t.held_days} for t in r["trades"]]),
                    width="stretch", hide_index=True)


st.divider()
st.caption(f"Source and the live paper-trading half: [{REPO_URL}]({REPO_URL}). "
           "Backtests are not predictions; most retail strategies lose money "
           "after costs. Not financial advice.")
