"""
Earnings blackout calendar.

WHY THIS MATTERS MORE THAN IT LOOKS
Earnings are the single most violent scheduled event for an individual stock —
a 10-15% overnight gap is routine. Mean reversion fading that gap is not a
strategy, it's a donation. And for PAIRS it's worse: if XOM reports and CVX
doesn't, the spread you're betting will converge blows apart on company-specific
news that has nothing to do with your thesis. Earnings are the classic killer of
energy-stock pairs trades.

So: don't open a new position into an earnings print. That's all this does.

TWO DATA SOURCES, DELIBERATELY
  history  -> SEC EDGAR, 8-K Item 2.02 ("Results of Operations"). This is the
              company filing its OWN results, so it's authoritative, precisely
              timestamped, free, and goes back decades. Perfect for backtesting.
  future   -> yfinance's forward earnings date. Necessarily an estimate; a
              company can and does move the date.

The split matters: backtests need accurate history (EDGAR), live bots need to
know what's coming (yfinance). Using a third-party calendar for BOTH would give
you worse history for no reason.

ETFs HAVE NO EARNINGS. USO, SPY, XLE etc. return nothing here and are silently
skipped — that's a correct no-op, not a failure. This module only does something
when you trade individual companies (XOM, CVX, COP...).

FIX vs previous version — live freshness:
  Dates were loaded once per symbol and never again, so a bot running for
  weeks would (a) never learn about a newly scheduled earnings date, and
  (b) keep treating a moved date as fixed. ensure() now refreshes on a TTL
  (default daily). Backtests still pay one fetch per symbol.
"""

from dataclasses import dataclass, field
import time as _time
import pandas as pd


@dataclass
class EarningsCalendar:
    """Veto source: is `symbol` inside an earnings blackout?

    before_days / after_days are calendar days around the announcement.
    Defaults (1, 1) cover the session before (pre-positioning), the print
    itself, and the session after (the gap). Widen if you want more distance.

    estimated_pad_days: forward-looking dates from yfinance are ESTIMATES and
    companies shift them. For any date flagged as an estimate we widen the
    window by this much, because being conservative around an uncertain date is
    the whole point.

    ttl_hours: how often a LIVE bot re-loads a symbol's dates (default daily).
    """
    edgar_client: object = None            # research.sec_filings.EdgarClient
    before_days: int = 1
    after_days: int = 1
    estimated_pad_days: int = 1
    ttl_hours: float = 24.0
    _dates: dict = field(default_factory=dict)      # symbol -> [(ts, is_estimate)]
    _loaded_at: dict = field(default_factory=dict)  # symbol -> epoch seconds

    # ---- loading --------------------------------------------------------
    def load_history(self, symbol: str) -> list[pd.Timestamp]:
        """Authoritative past earnings dates from EDGAR 8-K Item 2.02."""
        if self.edgar_client is None:
            return []
        try:
            return self.edgar_client.earnings_dates(symbol)
        except Exception as e:
            print(f"  EDGAR earnings lookup failed for {symbol} ({e}).")
            return []

    def load_future(self, symbol: str) -> list[pd.Timestamp]:
        """Upcoming earnings date(s) from yfinance. Estimates by nature."""
        try:
            import yfinance as yf
            t = yf.Ticker(symbol)
            df = t.get_earnings_dates(limit=8)
            if df is None or len(df) == 0:
                return []
            out = []
            for idx in df.index:
                ts = pd.Timestamp(idx)
                ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
                out.append(ts)
            return out
        except Exception:
            # yfinance has no earnings for ETFs, and its API shifts around.
            # Silently treat as "no known future earnings".
            return []

    def ensure(self, symbol: str, force: bool = False):
        """Populate dates for a symbol (EDGAR history + yfinance future).
        Refreshes automatically once the TTL has expired, so a long-running
        bot keeps seeing newly scheduled / moved earnings dates."""
        sym = symbol.upper()
        now = _time.time()
        fresh = (now - self._loaded_at.get(sym, 0.0)) < self.ttl_hours * 3600
        if sym in self._dates and fresh and not force:
            return
        entries = [(ts, False) for ts in self.load_history(sym)]
        utcnow = pd.Timestamp.utcnow()
        for ts in self.load_future(sym):
            if ts > utcnow and not any(abs((ts - e).days) < 2 for e, _ in entries):
                entries.append((ts, True))     # future -> estimate
        entries.sort(key=lambda x: x[0])
        self._dates[sym] = entries
        self._loaded_at[sym] = now

    def set_dates(self, symbol: str, dates, estimated=False):
        """Manual override — also what the tests use, and handy if you have a
        better earnings source. Marks the symbol as fresh so ensure() doesn't
        clobber it before the TTL expires."""
        entries = []
        for d in dates:
            ts = pd.Timestamp(d)
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
            entries.append((ts, estimated))
        entries.sort(key=lambda x: x[0])
        self._dates[symbol.upper()] = entries
        self._loaded_at[symbol.upper()] = _time.time()

    # ---- the veto -------------------------------------------------------
    def veto(self, ts, symbol: str) -> str:
        ts = pd.Timestamp(ts)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        sym = symbol.upper()
        self.ensure(sym)
        for when, is_est in self._dates.get(sym, []):
            pad = self.estimated_pad_days if is_est else 0
            lo = when.normalize() - pd.Timedelta(days=self.before_days + pad)
            hi = when.normalize() + pd.Timedelta(days=self.after_days + pad + 1)
            if lo <= ts <= hi:
                kind = "estimated " if is_est else ""
                return f"earnings blackout ({kind}{when:%Y-%m-%d})"
        return ""

    # ---- introspection --------------------------------------------------
    def known_dates(self, symbol: str) -> pd.DataFrame:
        sym = symbol.upper()
        self.ensure(sym)
        rows = [{"date": d, "estimated": e} for d, e in self._dates.get(sym, [])]
        return pd.DataFrame(rows)
