"""
Download historical news from Alpaca into a local cache, so backtests are fast,
offline, and reproducible.

Run this ONCE per symbol set / date range, then point backtests at the cache.

Examples:
  # Cache 3 years of news for the oil names:
  python run_news_fetch.py --symbols USO BNO XLE --start 2023-01-01 --out news_oil.json

  # Then backtest with the filter enabled (see NEWS_FILTER.md):
  python run_backtest.py --symbols USO --strategy meanrev --start 2023-01-01 \
      --news-cache news_oil.json --trades

Needs ALPACA_API_KEY / ALPACA_SECRET_KEY (the same paper keys you already have —
the News API is included with your Market Data plan).

FIX vs previous version — the 50-article truncation:
  Each Alpaca news request returns at most 50 articles, and the old loop made
  ONE request per 30-day window. A busy month (say, an OPEC cut plus a
  hurricane) easily exceeds 50 articles, and everything past the cap was
  silently dropped — precisely the high-news periods the filter most needs to
  know about. The fetch now paginates WITHIN each window: Alpaca returns
  newest-first, so when a full page of 50 comes back, we walk the window's end
  back to just before the oldest article received and request again, until the
  window is exhausted. The dedupe pass handles any overlap.

NOTE ON COVERAGE: Alpaca's news is Benzinga-sourced and equity/crypto oriented.
Coverage of ETFs like USO is decent; coverage of *futures* (CL) is basically
nonexistent. For crude, the practical approach is to use news tagged to the oil
ETFs and energy names as a proxy for the oil complex, and lean on the scheduled
event calendar (EIA/OPEC) — which needs no news feed at all — for the rest.
"""

import argparse
import pandas as pd
from data.news import AlpacaNews, save_cache

PAGE = 50   # Alpaca's per-request article cap


def fetch_window(client, sym, start, end) -> list:
    """All articles for sym in [start, end], paginating past the 50/request
    cap. Alpaca serves newest-first, so a full page means older articles were
    cut off: shrink the window's END to just before the oldest article we got
    and pull again."""
    out = []
    window_end = end
    while window_end > start:
        got = client.articles(sym, start, window_end)   # sorted ascending
        out.extend(got)
        if len(got) < PAGE:
            break
        oldest = min(a.created_at for a in got)
        new_end = oldest - pd.Timedelta(seconds=1)
        if new_end >= window_end:   # no progress; bail rather than loop
            break
        window_end = new_end
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", required=True)
    p.add_argument("--start", default="2023-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--out", default="news_cache.json")
    args = p.parse_args()

    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC") if args.end else pd.Timestamp.utcnow()

    client = AlpacaNews()
    data = {}
    for sym in args.symbols:
        print(f"Fetching news for {sym} ({start:%Y-%m-%d} .. {end:%Y-%m-%d}) ...")
        # Walk month by month, paginating inside each window.
        arts, cursor = [], start
        while cursor < end:
            chunk_end = min(cursor + pd.Timedelta(days=30), end)
            arts.extend(fetch_window(client, sym, cursor, chunk_end))
            cursor = chunk_end
        # Dedupe by (timestamp, headline).
        seen, unique = set(), []
        for a in arts:
            k = (a.created_at, a.headline)
            if k not in seen:
                seen.add(k)
                unique.append(a)
        unique.sort(key=lambda a: a.created_at)
        data[sym] = unique
        print(f"  {len(unique)} articles")

    n = save_cache(args.out, data)
    print(f"\nSaved {n} articles -> {args.out}")
    print("Point a backtest at it with --news-cache " + args.out)


if __name__ == "__main__":
    main()
