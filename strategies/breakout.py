"""
Donchian channel breakout — classic trend following.

Enter long when price closes above the highest close of the last `entry`
bars; exit when it closes below the lowest close of the last `exit` bars
(and mirrored for shorts). The asymmetry (wide entry, tighter exit) is the
whole design: you're late to every trend on purpose, you lose small and
often, and the rare monster trend pays for everything. This is the
most-validated strategy family in futures — natural for the IBKR/CL side —
and a useful diversifier for a mean-reversion book, since trend and
mean-rev tend to bleed at different times.

Expectations to set BEFORE running it: win rate around 30-45% with a fat
right tail is normal and healthy. If you judge it by win rate you'll turn
it off right before it works.

Duck-typed to the project's Strategy interface (.name, .params, .on_bar).
"""


class Breakout:
    def __init__(self, symbols, entry=55, exit=20, allow_short=True):
        """
        entry : channel length for new positions (55 = classic Turtle S2).
        exit  : channel length for exits; shorter than entry by design.
        """
        self.symbols = list(symbols)
        # Constructor args, as Strategy.__init__ records them; the backtest
        # and walk-forward tools size their warmup from this.
        self.params = dict(entry=entry, exit=exit, allow_short=allow_short)
        self.entry = entry
        self.exit = exit
        self.allow_short = allow_short
        self.name = f"Breakout({entry}/{exit}{'' if allow_short else ',long-only'})"

    def on_bar(self, ctx) -> dict:
        targets = {}
        for s in self.symbols:
            h = ctx.history.get(s)
            if h is None or len(h) < self.entry + 1:
                continue
            c = h["close"]
            px = float(c.iloc[-1])
            # Channels EXCLUDE the current bar, so "close above the channel"
            # is a genuine breakout, not price compared against itself.
            hi_entry = float(c.iloc[-self.entry - 1:-1].max())
            lo_entry = float(c.iloc[-self.entry - 1:-1].min())
            hi_exit = float(c.iloc[-self.exit - 1:-1].max())
            lo_exit = float(c.iloc[-self.exit - 1:-1].min())

            pos = ctx.positions.get(s, 0.0)
            if pos > 0:                     # long: hold until exit-channel low
                targets[s] = 0.0 if px < lo_exit else 1.0
            elif pos < 0:                   # short: hold until exit-channel high
                targets[s] = 0.0 if px > hi_exit else -1.0
            else:                           # flat: look for a breakout
                if px > hi_entry:
                    targets[s] = 1.0
                elif self.allow_short and px < lo_entry:
                    targets[s] = -1.0
                else:
                    targets[s] = 0.0
        return targets
