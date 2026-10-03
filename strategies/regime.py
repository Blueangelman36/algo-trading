"""
Regime filter — a wrapper that vetoes NEW entries when market conditions
are hostile to the wrapped strategy's style, and NEVER blocks exits.

Same philosophy as the catalyst filters (EIA/OPEC calendar, news veto,
earnings blackout): defensive only. The classic failure mode of mean
reversion is fading a move that keeps going — a trending regime. The
classic failure mode of trend following is chop. This wrapper measures
which regime we're in and simply refuses to open positions into the
wrong one.

Regime measure: Kaufman's Efficiency Ratio over `window` bars —
    ER = |net price change| / sum(|bar-to-bar changes|)
ER near 1.0 = clean directional trend; near 0.0 = pure chop.

Usage:
    strat = RegimeFilter(MeanReversion(["USO"], ...), mode="mean_reversion")
    strat = RegimeFilter(Momentum(["SPY"], ...),      mode="trend")

Rules (per symbol):
  - mode="mean_reversion": veto new entries when ER > er_max (too trendy).
  - mode="trend":          veto new entries when ER < er_min (too choppy).
  - Currently flat + vetoed          -> stay flat.
  - Currently positioned, same side  -> pass the weight through (holding
                                        is not entering).
  - Signal flips against a held side -> flatten to 0.0 instead of flipping
                                        (the exit half executes, the new
                                        entry half is vetoed).
"""


class RegimeFilter:
    def __init__(self, inner, mode="mean_reversion", window=50,
                 er_max=0.35, er_min=0.25):
        self.inner = inner
        self.mode = mode
        self.window = window
        self.er_max = er_max
        self.er_min = er_min
        self.name = f"{inner.name} +RegimeFilter({mode},ER{window})"
        # Pass through symbols if the inner strategy exposes them.
        self.symbols = getattr(inner, "symbols", None)

    def _efficiency_ratio(self, closes) -> float | None:
        if len(closes) < self.window + 1:
            return None
        w = closes.iloc[-(self.window + 1):]
        net = abs(float(w.iloc[-1]) - float(w.iloc[0]))
        path = float(w.diff().abs().sum())
        if path <= 0:
            return None
        return net / path

    def _entries_vetoed(self, er: float | None) -> bool:
        if er is None:
            return False   # not enough data to judge: don't veto
        if self.mode == "mean_reversion":
            return er > self.er_max      # market too trendy to fade
        if self.mode == "trend":
            return er < self.er_min      # market too choppy to follow
        return False

    def on_bar(self, ctx) -> dict:
        targets = self.inner.on_bar(ctx)
        out = {}
        for symbol, weight in targets.items():
            h = ctx.history.get(symbol)
            if h is None or h.empty:
                out[symbol] = weight
                continue
            er = self._efficiency_ratio(h["close"])
            if not self._entries_vetoed(er):
                out[symbol] = weight
                continue

            pos = ctx.positions.get(symbol, 0.0)
            if pos == 0:
                out[symbol] = 0.0                     # veto the new entry
            elif (pos > 0) == (weight > 0) and weight != 0:
                out[symbol] = weight                  # holding, not entering
            else:
                out[symbol] = 0.0                     # exit yes, flip no
        return out
