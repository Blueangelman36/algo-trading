"""
SEC EDGAR filings — free, official, no API key.

WHAT IT'S FOR
Same defensive logic as the news filter: an 8-K means "something material just
happened at this company." That is precisely the moment a mean-reversion bot
should NOT be fading the move. This gives your bots a veto source grounded in
official filings rather than headlines.

WHY EDGAR IS BETTER THAN LLM SENTIMENT FOR BACKTESTING
A filing's existence and timestamp are OBJECTIVE FACTS. "Did XOM file an 8-K
before 14:30 on 2024-03-06?" has one true answer, and it's the same answer today
as it was then. Contrast that with LLM sentiment scoring, where the model has
already seen the aftermath and quietly leaks hindsight into your backtest.
EDGAR data can't cheat like that. Everything here is honestly backtestable.

FORMS THAT MATTER
  8-K   material event (earnings release, M&A, exec departure, disaster).
        The single most useful form for this purpose. Item 2.02 specifically =
        "Results of Operations" = the earnings announcement.
  10-Q  quarterly report      10-K  annual report
  4     insider transaction (director/officer bought or sold)
  13D/G activist / large stake

SEC REQUIREMENTS (non-negotiable — you WILL get 403'd otherwise)
  - Every request must carry a User-Agent identifying you, with a contact email.
    Set it via env var:   SEC_USER_AGENT="Your Name your@email.com"
  - Max 10 requests/second. This client sleeps 0.12s between calls and caches
    aggressively, so normal use stays far under.

Filings are cached to disk. A company's filing history changes only when they
file something new, so caching costs you nothing and keeps you polite.

FIX vs previous version — live freshness:
  SecVetoSource used to load a symbol's filings ONCE (at first veto check) and
  hold them in memory forever. A bot running for weeks under systemd would
  therefore never see any NEW 8-K — the veto was only as fresh as startup,
  which defeats the point of a filings-based safety net. It now refreshes each
  symbol's filings from EDGAR on a TTL (default every 6 hours), bypassing the
  disk cache on refresh. Backtests are unaffected: a backtest finishes long
  before the first TTL expiry, so it still costs one fetch per symbol.
"""

from dataclasses import dataclass
import json
import os
import time
import urllib.request
import pandas as pd

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
MIN_INTERVAL = 0.12          # seconds between requests (SEC allows 10/sec)

# Forms we treat as "a catalyst happened".
MATERIAL_FORMS = {"8-K", "8-K/A"}
# Forms worth knowing about but not necessarily vetoing on.
NOTABLE_FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A", "4", "SC 13D", "SC 13G"}

# 8-K item codes worth calling out. 2.02 is the earnings release.
ITEM_MEANINGS = {
    "1.01": "material agreement", "1.02": "termination of agreement",
    "2.02": "results of operations (EARNINGS)", "2.01": "completion of acquisition",
    "2.05": "costs from exit/disposal", "2.06": "material impairment",
    "3.01": "delisting notice", "4.01": "auditor change",
    "4.02": "non-reliance on prior financials", "5.02": "exec/director change",
    "7.01": "regulation FD disclosure", "8.01": "other events",
}


@dataclass
class Filing:
    form: str
    filed_at: pd.Timestamp      # tz-aware UTC (date-level precision from EDGAR)
    items: list                 # 8-K item codes, e.g. ["2.02","9.01"]
    accession: str

    @property
    def is_earnings(self) -> bool:
        return any(i.startswith("2.02") for i in self.items)

    def describe(self) -> str:
        meanings = [ITEM_MEANINGS.get(i.split()[0], "") for i in self.items]
        meanings = [m for m in meanings if m]
        return f"{self.form}" + (f" — {'; '.join(meanings)}" if meanings else "")

    def to_dict(self):
        return {"form": self.form, "filed_at": self.filed_at.isoformat(),
                "items": self.items, "accession": self.accession}

    @staticmethod
    def from_dict(d):
        ts = pd.Timestamp(d["filed_at"])
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return Filing(d["form"], ts.tz_convert("UTC"), d.get("items", []),
                      d.get("accession", ""))


class EdgarClient:
    def __init__(self, user_agent=None, cache_dir=".edgar_cache"):
        self.ua = user_agent or os.environ.get("SEC_USER_AGENT")
        if not self.ua or "@" not in self.ua:
            raise SystemExit(
                "SEC requires a User-Agent with a contact email.\n"
                '  Set it, e.g.:  SEC_USER_AGENT="Your Name you@example.com"\n'
                "Without it EDGAR returns 403 and may block your IP.")
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self._last_call = 0.0
        self._cik_map = None

    def _get(self, url: str) -> dict:
        # Politeness: never exceed the SEC's 10 req/sec.
        gap = time.time() - self._last_call
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)
        req = urllib.request.Request(url, headers={
            "User-Agent": self.ua,
            "Accept-Encoding": "gzip, deflate",
            "Host": url.split("/")[2],
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        finally:
            self._last_call = time.time()

    # ---- ticker -> CIK --------------------------------------------------
    def cik_for(self, ticker: str) -> str | None:
        """EDGAR keys everything by CIK, not ticker. CIK must be zero-padded
        to 10 digits in URLs."""
        if self._cik_map is None:
            path = os.path.join(self.cache_dir, "company_tickers.json")
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
            else:
                data = self._get(SEC_TICKERS_URL)
                with open(path, "w") as f:
                    json.dump(data, f)
            self._cik_map = {v["ticker"].upper(): str(v["cik_str"]).zfill(10)
                             for v in data.values()}
        return self._cik_map.get(ticker.upper())

    # ---- filings --------------------------------------------------------
    def filings(self, ticker: str, refresh=False) -> list[Filing]:
        """Recent filings for a ticker (EDGAR's 'recent' block: roughly the
        last 1000 filings). Cached to disk; refresh=True re-fetches.

        Returns [] for ETFs and anything without a CIK — USO, SPY etc. don't
        file 8-Ks, and that's a normal no-op, not an error."""
        cache = os.path.join(self.cache_dir, f"{ticker.upper()}.json")
        if os.path.exists(cache) and not refresh:
            with open(cache) as f:
                return [Filing.from_dict(d) for d in json.load(f)]

        cik = self.cik_for(ticker)
        if not cik:
            return []
        try:
            data = self._get(SEC_SUBMISSIONS.format(cik=cik))
        except Exception as e:
            print(f"  EDGAR fetch failed for {ticker} ({e}); treating as no filings.")
            # On a failed refresh, fall back to whatever the disk cache has —
            # stale data beats no data for a veto source.
            if os.path.exists(cache):
                with open(cache) as f:
                    return [Filing.from_dict(d) for d in json.load(f)]
            return []

        recent = (data.get("filings") or {}).get("recent") or {}
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        items = recent.get("items", [])
        accs = recent.get("accessionNumber", [])

        out = []
        for i, form in enumerate(forms):
            try:
                ts = pd.Timestamp(dates[i]).tz_localize("UTC")
            except (IndexError, ValueError):
                continue
            raw_items = items[i] if i < len(items) else ""
            item_list = [x.strip() for x in (raw_items or "").split(",") if x.strip()]
            out.append(Filing(form, ts, item_list,
                              accs[i] if i < len(accs) else ""))
        out.sort(key=lambda f: f.filed_at)

        with open(cache, "w") as f:
            json.dump([x.to_dict() for x in out], f)
        return out

    def earnings_dates(self, ticker: str) -> list[pd.Timestamp]:
        """Historical earnings announcement dates, taken from 8-K Item 2.02
        filings. This is the AUTHORITATIVE source — it's the company's own
        filing of its results — and it goes back to the 1990s, which makes it
        far better for backtesting than any third-party earnings calendar."""
        return [f.filed_at for f in self.filings(ticker) if f.is_earnings]


class SecVetoSource:
    """Veto source: was a material filing made within `lookback_days` of now?

    Note EDGAR gives filing DATE, not time-of-day. We therefore treat a filing
    as active for the whole day it was filed (plus lookback). That's
    intentionally conservative — being a few hours early on a veto costs you a
    trade; being late costs you money.

    ttl_hours: how often a LIVE bot re-fetches a symbol's filings from EDGAR.
    The first load uses the disk cache (fast startup, fine for backtests);
    every TTL expiry after that bypasses it, so new 8-Ks are actually seen.
    """

    def __init__(self, client: EdgarClient, lookback_days=1,
                 forms=None, cache: dict | None = None, ttl_hours=6):
        self.client = client
        self.lookback_days = lookback_days
        self.forms = forms or MATERIAL_FORMS
        self.ttl_hours = ttl_hours
        self._cache = cache if cache is not None else {}
        self._loaded_at: dict[str, float] = {}

    def _filings(self, symbol):
        now = time.time()
        loaded = self._loaded_at.get(symbol, 0.0)
        expired = (now - loaded) > self.ttl_hours * 3600
        if symbol not in self._cache or expired:
            # First load may serve from disk cache; refreshes bypass it.
            first_load = symbol not in self._cache
            self._cache[symbol] = self.client.filings(symbol,
                                                      refresh=not first_load)
            self._loaded_at[symbol] = now
        return self._cache[symbol]

    def veto(self, ts, symbol: str) -> str:
        ts = pd.Timestamp(ts)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        lo = ts.normalize() - pd.Timedelta(days=self.lookback_days)
        for f in self._filings(symbol):
            if f.form not in self.forms:
                continue
            # Filing is date-level; treat it as active through end of its day.
            if lo <= f.filed_at <= ts.normalize() + pd.Timedelta(days=1):
                return f"SEC filing: {f.describe()} ({f.filed_at:%Y-%m-%d})"
        return ""
