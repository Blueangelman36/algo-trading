"""
NewsFilter — wraps any Strategy and vetoes ENTRIES around news and scheduled
events. It never blocks an exit.

THE IDEA
Mean reversion fades price moves: it assumes a 2-sigma deviation is noise that
will snap back. There is exactly one situation where that assumption is
catastrophically wrong — when the move is caused by real news. If crude gaps 4%
on an OPEC production cut, that deviation is *information*, not noise, and it is
not coming back. The bot fades it, gets run over, and averages into a loser.

So this filter's job is defensive: when a catalyst is present, don't open a new
fade. Wait for the dust to settle.

TWO SAFETY PROPERTIES, both tested:
  1. A veto NEVER blocks a close. If the strategy wants to go flat, it goes
     flat. Trapping a bot in a position "for its own protection" would be a
     far worse bug than the one we're preventing.
  2. For coupled strategies (pairs), vetoes are ALL-OR-NOTHING. Blocking one
     leg of a pair while opening the other leaves you with a naked, unhedged
     position — the exact opposite of market-neutral. If any opening leg is
     vetoed, the whole entry is suppressed.

FIX vs previous version — vetoed FLIPS now flatten:
  A flip (long -> short) is an exit AND an entry in one order. The old code
  treated the whole flip as an "opening" and, when vetoed, dropped the target
  entirely — which silently KEPT the old position open straight through the
  catalyst. That violated property 1 in spirit: the exit half of the flip was
  being blocked. Now a vetoed flip emits weight 0.0 instead: the exit half
  executes, only the new entry half is suppressed.

VETO TRIGGERS
  - Scheduled event blackout (EIA inventories, OPEC, rig count) from
    research/event_calendar.py
  - News intensity: >= min_articles articles on that symbol in the last
    news_window_min minutes
  - High-impact keyword in a recent headline (see HIGH_IMPACT)

ON SENTIMENT AND HINDSIGHT (read this before "improving" it with an LLM)
The keyword scorer below is deliberately dumb, deterministic, and rules-based.
That is a feature. If you score a 2019 headline with a modern LLM, the model
already knows what happened next — it was trained on the aftermath. Your
"sentiment signal" is then quietly contaminated with hindsight, your backtest
looks brilliant, and it dies live. A dumb keyword list has no such advantage,
so what it shows you in a backtest is closer to what you'd actually have got.

Not financial advice.
"""

import re
import pandas as pd

# Words that suggest a genuine supply/demand catalyst rather than routine chatter.
HIGH_IMPACT = [
    # supply shocks
    "opec", "production cut", "output cut", "production increase", "quota",
    "sanction", "embargo", "export ban", "supply disruption", "outage",
    "pipeline", "refinery fire", "explosion", "strike", "force majeure",
    # geopolitics
    "war", "attack", "missile", "invasion", "conflict", "blockade",
    "hormuz", "red sea",
    # scheduled data / weather
    "inventories", "inventory build", "inventory draw", "stockpiles",
    "hurricane", "storm",
    # macro
    "recession", "demand forecast", "opec+",
]
HIGH_IMPACT_RE = re.compile("|".join(re.escape(w) for w in HIGH_IMPACT), re.I)


def keyword_hits(text: str) -> list[str]:
    """Deterministic, hindsight-free. Returns the matched keywords."""
    return sorted({m.group(0).lower() for m in HIGH_IMPACT_RE.finditer(text or "")})


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


class NewsFilter:
    """Wraps a Strategy. Same interface (on_bar -> {symbol: weight}), so it
    drops into the backtest engine and the live loop unchanged."""

    def __init__(self, inner, news_provider=None, calendar=None,
                 news_window_min=120, min_articles=3,
                 veto_on_event=True, veto_on_news=True, veto_on_keyword=True,
                 coupled=None, verbose=True, extra_sources=None):
        self.inner = inner
        self.symbols = inner.symbols
        self.params = dict(getattr(inner, "params", {}))
        self.news = news_provider
        self.calendar = calendar
        self.news_window_min = news_window_min
        self.min_articles = min_articles
        self.veto_on_event = veto_on_event
        self.veto_on_news = veto_on_news
        self.veto_on_keyword = veto_on_keyword
        # Pairs (and any multi-leg strategy) must be all-or-nothing.
        self.coupled = (len(inner.symbols) > 1) if coupled is None else coupled
        self.verbose = verbose
        # Any object with .veto(ts, symbol) -> str. Lets you plug in the
        # earnings calendar, SEC filings, or anything else you write later
        # without touching this class.
        self.extra_sources = list(extra_sources or [])
        self.vetoes: list[dict] = []      # audit trail

    @property
    def name(self) -> str:
        return f"NewsFiltered({self.inner.name})"

    # ---- veto assessment ------------------------------------------------
    def _veto_reason(self, ts, symbol: str) -> str:
        """Return a reason string if this symbol is currently vetoed, else ''."""
        if self.veto_on_event and self.calendar is not None:
            blocked, why = self.calendar.blackout(ts, symbol)
            if blocked:
                return f"event blackout: {why}"

        for src in self.extra_sources:
            try:
                why = src.veto(ts, symbol)
            except Exception as e:
                print(f"  [news filter] veto source {type(src).__name__} failed: {e}")
                why = ""
            if why:
                return why

        if self.news is None:
            return ""

        ts = pd.Timestamp(ts)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        start = ts - pd.Timedelta(minutes=self.news_window_min)
        # Strictly <= ts: the bot may never see an article from the future.
        arts = self.news.articles(symbol, start, ts)

        if self.veto_on_news and len(arts) >= self.min_articles:
            return f"news intensity: {len(arts)} articles in {self.news_window_min}m"

        if self.veto_on_keyword:
            for a in arts:
                hits = keyword_hits(f"{a.headline} {a.summary}")
                if hits:
                    return f"catalyst keyword {hits[:3]}: {a.headline[:60]!r}"
        return ""

    # ---- the wrapped decision -------------------------------------------
    def on_bar(self, ctx) -> dict:
        targets = dict(self.inner.on_bar(ctx))
        if not targets:
            return targets

        ts = None
        for sym in self.symbols:
            h = ctx.hist(sym)
            if h is not None and len(h):
                ts = h.index[-1]
                break
        if ts is None:
            return targets

        opening, closing = {}, {}
        for sym, w in targets.items():
            held = ctx.positions.get(sym, 0.0)
            # w == 0 means "go flat" -> always allowed, never vetoed.
            # Opening = no position yet, or flipping to the other side.
            is_opening = (w != 0) and (held == 0 or _sign(w) != _sign(held))
            (opening if is_opening else closing)[sym] = w

        if not opening:
            return targets      # nothing but exits/holds; let them all through

        # Assess each opening leg.
        reasons = {}
        for sym in opening:
            r = self._veto_reason(ts, sym)
            if r:
                reasons[sym] = r

        if not reasons:
            return targets      # clear to open

        # Coupled (pairs): if ANY opening leg is vetoed, drop ALL opening legs,
        # otherwise we'd open a naked, unhedged leg.
        if self.coupled:
            blocked = set(opening)
            why = "; ".join(f"{s}: {r}" for s, r in reasons.items())
        else:
            blocked = set(reasons)
            why = "; ".join(f"{s}: {r}" for s, r in reasons.items())

        for sym in blocked:
            self.vetoes.append({"timestamp": ts, "symbol": sym,
                                "reason": reasons.get(sym, "coupled with vetoed leg")})
        if self.verbose:
            print(f"  [news filter] VETO entry ({', '.join(sorted(blocked))}) — {why}")

        # Exits always survive. Blocked pure entries (currently flat) are
        # simply not emitted, so the engine leaves those symbols flat.
        # Blocked FLIPS are converted to a flatten (0.0): the exit half of
        # the flip executes, only the new entry half is suppressed. The old
        # behavior dropped the target entirely, which held the OLD position
        # open through the catalyst — the exact thing property 1 forbids.
        out = {}
        for s, w in targets.items():
            if s not in blocked:
                out[s] = w
            else:
                held = ctx.positions.get(s, 0.0)
                if held != 0 and _sign(w) != _sign(held):
                    out[s] = 0.0    # vetoed flip -> exit to flat instead
        return out

    # ---- reporting ------------------------------------------------------
    def veto_report(self) -> pd.DataFrame:
        if not self.vetoes:
            return pd.DataFrame(columns=["timestamp", "symbol", "reason"])
        return pd.DataFrame(self.vetoes)
