"""
Pyramiding breakout — Donchian entries that ADD to winners.

Plain breakout (strategies/breakout.py) takes full size at the first
breakout. Pyramiding instead starts small and scales in as the trend proves
itself: each time price moves add_atr ATRs further in your favor, another
unit goes on, up to max_adds. The classic Turtle construction. The result:
losing breakouts are punished at 1/(1+max_adds) of full size, while the
rare monster trend ends up carrying maximum size — exactly the asymmetry
that produces the "larger wins over time" profile. The cost is deeper
drawdowns when a fully-pyramided trend reverses, which is why the ATR stop
below is not optional decoration.

Position sizing via weights: with max_adds=3, the first unit is weight 0.25,
and each add raises it by 0.25 to a max of 1.0. The engine/trader converts
weight into dollars as usual (w * equity * max_gross_per_symbol).

Exits, whichever comes first:
  - Channel exit: close crosses the `exit`-bar channel against the position
    (the standard trend-is-over signal).
  - ATR stop: close falls stop_atr ATRs below the LAST add price (for longs;
    mirrored for shorts). Anchoring the stop to the last add — not the
    original entry — is what keeps a fully-pyramided position from giving
    everything back.
Exits always liquidate the ENTIRE pyramid, never one unit at a time.

Expectations: win rate will look worse than plain breakout (small probing
entries get stopped), average win much larger. Judge it on expectancy and
Calmar over a long window, not win rate. Run it through walk-forward before
trusting any parameter set.

Duck-typed to the project's Strategy interface (.name, .params, .on_bar).
"""


class PyramidingBreakout:
    def __init__(self, symbols, entry=55, exit=20, allow_short=True,
                 max_adds=3, add_atr=1.0, stop_atr=2.0, atr_window=20):
        """
        entry/exit : Donchian channel lengths, as in Breakout.
        max_adds   : additional units after the initial entry (3 => up to
                     4 units total).
        add_atr    : favorable movement (in ATRs) required per add.
        stop_atr   : hard stop distance in ATRs from the most recent add.
        atr_window : ATR lookback.
        """
        self.symbols = list(symbols)
        # Constructor args, as Strategy.__init__ records them; the backtest
        # and walk-forward tools size their warmup from this.
        self.params = dict(entry=entry, exit=exit, allow_short=allow_short,
                           max_adds=max_adds, add_atr=add_atr,
                           stop_atr=stop_atr, atr_window=atr_window)
        self.entry = entry
        self.exit = exit
        self.allow_short = allow_short
        self.max_adds = max(0, int(max_adds))
        self.add_atr = add_atr
        self.stop_atr = stop_atr
        self.atr_window = atr_window
        self.name = (f"PyrBreakout({entry}/{exit},adds={max_adds}"
                     f"@{add_atr}ATR,stop={stop_atr}ATR)")
        # symbol -> {"side": +1/-1, "last_add": px, "adds": n, "atr": atr}
        self._state: dict = {}

    # ---- indicators ------------------------------------------------------
    def _atr(self, h) -> float | None:
        """Average True Range over atr_window bars."""
        if len(h) < self.atr_window + 1:
            return None
        hi, lo, cl = h["high"], h["low"], h["close"]
        prev_close = cl.shift(1)
        tr1 = hi - lo
        tr2 = (hi - prev_close).abs()
        tr3 = (lo - prev_close).abs()
        tr = tr1.combine(tr2, max).combine(tr3, max)
        atr = float(tr.iloc[-self.atr_window:].mean())
        return atr if atr > 0 else None

    def _weight(self, st) -> float:
        return st["side"] * (1 + st["adds"]) / (1 + self.max_adds)

    # ---- main ------------------------------------------------------------
    def on_bar(self, ctx) -> dict:
        targets = {}
        for s in self.symbols:
            h = ctx.history.get(s)
            if h is None or len(h) < max(self.entry, self.atr_window) + 1:
                continue
            c = h["close"]
            px = float(c.iloc[-1])
            # Channels EXCLUDE the current bar (a breakout must beat the
            # PAST, not itself).
            hi_entry = float(c.iloc[-self.entry - 1:-1].max())
            lo_entry = float(c.iloc[-self.entry - 1:-1].min())
            hi_exit = float(c.iloc[-self.exit - 1:-1].max())
            lo_exit = float(c.iloc[-self.exit - 1:-1].min())
            atr = self._atr(h)

            st = self._state.get(s)

            if st is None:
                # Flat: look for a fresh breakout.
                if px > hi_entry and atr:
                    st = {"side": 1, "last_add": px, "adds": 0, "atr": atr}
                    self._state[s] = st
                    targets[s] = self._weight(st)
                elif self.allow_short and px < lo_entry and atr:
                    st = {"side": -1, "last_add": px, "adds": 0, "atr": atr}
                    self._state[s] = st
                    targets[s] = self._weight(st)
                else:
                    targets[s] = 0.0
                continue

            side, a = st["side"], st["atr"]

            # 1) Hard ATR stop from the most recent add.
            stopped = (side > 0 and px < st["last_add"] - self.stop_atr * a) or \
                      (side < 0 and px > st["last_add"] + self.stop_atr * a)
            # 2) Channel exit.
            channel_out = (side > 0 and px < lo_exit) or \
                          (side < 0 and px > hi_exit)
            if stopped or channel_out:
                self._state.pop(s, None)
                targets[s] = 0.0
                continue

            # 3) Pyramid: add a unit per add_atr ATRs of favorable movement.
            #    ATR is frozen at entry (Turtle-style) so add spacing and the
            #    stop don't shrink as vol collapses inside a trend.
            while st["adds"] < self.max_adds:
                next_trigger = st["last_add"] + side * self.add_atr * a
                if (side > 0 and px >= next_trigger) or \
                   (side < 0 and px <= next_trigger):
                    st["adds"] += 1
                    st["last_add"] = next_trigger
                else:
                    break

            targets[s] = self._weight(st)
        return targets
