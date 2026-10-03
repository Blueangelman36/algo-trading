"""
Scheduled-event calendar — mainly for commodities, where the big movers are on
a known timetable.

The point of this module is NOT to predict events. It's to make sure a bot
doesn't walk into one. Opening a fresh mean-reversion position twenty minutes
before a crude inventory print is a coin flip you didn't intend to take; this
tells the bot to stand aside.

Events covered:
  EIA Weekly Petroleum Status Report — Wednesdays 10:30 ET. Crude/gasoline
      inventories. Routinely moves oil 2-3% within minutes. Shifts to THURSDAY
      when a federal holiday falls earlier in the report week (see below).
  Baker Hughes Rig Count — Fridays 13:00 ET. Milder, but a real mover.
  OPEC / OPEC+ meetings — irregular; supplied as explicit dates (see OPEC_DATES).
      Update these from opec.org press releases. Each monthly meeting
      announces only the next one, about a month ahead; each OPEC and
      non-OPEC Ministerial Meeting sets the next ministerial ~6 months out.
  EIA Natural Gas Storage — Thursdays 10:30 ET (matters for NG, not crude).

All times are US/Eastern internally and compared in UTC, so this behaves
correctly on your UTC droplet.

FIXES vs previous version:
  - Holiday shift generalized: the old code only shifted the EIA report when
    MONDAY was a holiday. EIA's actual practice is to delay the report by a
    day when a federal holiday falls on Monday THROUGH Wednesday of the report
    week (e.g. Veterans Day on a Wednesday pushes it to Thursday). The holiday
    list now carries all federal holidays for 2026-27, not just Mondays.
  - The stale-OPEC warning now actually fires: EventCalendar.blackout() prints
    a one-time warning when it's asked about a date beyond the last hardcoded
    OPEC meeting, instead of silently pretending there are no meetings.

IMPORTANT: OPEC dates are a hardcoded list and WILL go stale. Anything past the
list simply isn't covered — refresh OPEC_DATES from opec.org periodically.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import pandas as pd

ET = "America/New_York"

# Symbols each event class is relevant to. Extend as you add instruments.
CRUDE_SYMBOLS = {"USO", "BNO", "USL", "CL", "CL=F", "XLE", "XOP", "OIH", "DBO"}
NATGAS_SYMBOLS = {"UNG", "BOIL", "KOLD", "NG", "NG=F"}

# OPEC / OPEC+ ministerial meeting dates. UPDATE THESE — see module docstring.
# Format: YYYY-MM-DD. Treated as all-day high-risk events.
# Checked against opec.org press releases on 2026-10-03:
#   - 2026-01-04 .. 2026-10-04: monthly meetings of the participating
#     countries (eight, seven since the UAE's exit), each announced by the
#     one before; JMMCs (1 Feb, 5 Apr, 7 Jun) and the 41st ONOMM (7 Jun)
#     fell on the same days.
#   - 2026-11-01: EXPECTED, not yet announced. Every 2026 meeting so far was
#     on the first Sunday of the month; the 4 October release should confirm.
#   - 2026-11-29: the 42nd OPEC and non-OPEC Ministerial Meeting, set by the
#     41st. Expect it to set the 2027 schedule.
OPEC_DATES = [
    "2026-01-04", "2026-02-01", "2026-03-01", "2026-04-05",
    "2026-05-03", "2026-06-07", "2026-07-05", "2026-08-02",
    "2026-09-06", "2026-10-04", "2026-11-01",
    "2026-11-29",  # asof:opec-last
]

# US federal holidays (observed dates), 2026-2027. Any of these landing on
# Mon-Wed of a report week pushes the EIA petroleum report to Thursday.
US_FEDERAL_HOLIDAYS = [
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-10-12", "2026-11-11", "2026-11-26",
    "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-10-11", "2027-11-11", "2027-11-25",
    "2027-12-24",
]


@dataclass
class Event:
    name: str
    when: pd.Timestamp        # tz-aware, UTC
    symbols: set              # which instruments it affects; empty set = all
    severity: str             # 'high' | 'medium'

    def __repr__(self):
        return f"<{self.name} {self.when:%Y-%m-%d %H:%M %Z} ({self.severity})>"


def _et(d: date, t: time) -> pd.Timestamp:
    """Build a tz-aware UTC timestamp from an Eastern-time date+time."""
    return pd.Timestamp(datetime.combine(d, t)).tz_localize(ET).tz_convert("UTC")


def _fed_holidays() -> set:
    return {pd.Timestamp(d).date() for d in US_FEDERAL_HOLIDAYS}


def eia_petroleum_day(week_of: date) -> date:
    """The EIA petroleum report day for the week containing `week_of`:
    normally Wednesday, but Thursday when a federal holiday falls on
    Monday-Wednesday of that week (EIA delays the release by one day)."""
    monday = week_of - timedelta(days=week_of.weekday())
    wednesday = monday + timedelta(days=2)
    hols = _fed_holidays()
    if any((monday + timedelta(days=i)) in hols for i in range(3)):
        return wednesday + timedelta(days=1)   # slips to Thursday
    return wednesday


def events_between(start, end) -> list[Event]:
    """All known scheduled events in [start, end]. Accepts naive or tz-aware
    timestamps; naive is treated as UTC."""
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    if end.tzinfo is None:
        end = end.tz_localize("UTC")

    out: list[Event] = []
    day = start.tz_convert(ET).date()
    last = end.tz_convert(ET).date()

    opec = {pd.Timestamp(d).date() for d in OPEC_DATES}

    while day <= last:
        wd = day.weekday()  # Mon=0

        # EIA petroleum: the week's report day, at 10:30 ET
        if day == eia_petroleum_day(day):
            out.append(Event("EIA Petroleum Status Report",
                             _et(day, time(10, 30)), set(CRUDE_SYMBOLS), "high"))

        # EIA natural gas storage: Thursdays 10:30 ET
        if wd == 3:
            out.append(Event("EIA Natural Gas Storage",
                             _et(day, time(10, 30)), set(NATGAS_SYMBOLS), "high"))

        # Baker Hughes rig count: Fridays 13:00 ET
        if wd == 4:
            out.append(Event("Baker Hughes Rig Count",
                             _et(day, time(13, 0)), set(CRUDE_SYMBOLS), "medium"))

        # OPEC meetings: all-day, anchored at 08:00 ET
        if day in opec:
            out.append(Event("OPEC/OPEC+ Meeting",
                             _et(day, time(8, 0)), set(CRUDE_SYMBOLS), "high"))

        day += timedelta(days=1)

    out = [e for e in out if start <= e.when <= end]
    out.sort(key=lambda e: e.when)
    return out


def stale_after() -> pd.Timestamp:
    """The date past which OPEC coverage is unreliable (last hardcoded date).
    Callers should warn if they're operating beyond this."""
    return pd.Timestamp(max(OPEC_DATES)).tz_localize("UTC")


class EventCalendar:
    """Answers one question: is `symbol` inside a blackout window right now?

    before_min / after_min define the window around each event. Defaults are
    deliberately asymmetric: a wide pre-event window (don't walk in), and a
    shorter post-event one (let the dust settle, then resume). OPEC (all-day)
    gets a much wider window.
    """

    def __init__(self, before_min=60, after_min=45,
                 opec_before_min=240, opec_after_min=240,
                 include_medium=False):
        self.before = before_min
        self.after = after_min
        self.opec_before = opec_before_min
        self.opec_after = opec_after_min
        self.include_medium = include_medium
        self._cache: dict[tuple, list[Event]] = {}
        self._stale_warned = False

    def _events_for_day(self, ts: pd.Timestamp) -> list[Event]:
        key = ts.tz_convert("UTC").date()
        if key not in self._cache:
            lo = pd.Timestamp(key).tz_localize("UTC") - pd.Timedelta(days=1)
            hi = pd.Timestamp(key).tz_localize("UTC") + pd.Timedelta(days=2)
            self._cache[key] = events_between(lo, hi)
        return self._cache[key]

    def blackout(self, ts, symbol: str) -> tuple[bool, str]:
        """Returns (is_blackout, reason). reason is '' when clear."""
        ts = pd.Timestamp(ts)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        sym = symbol.upper()

        # OPEC coverage is only as fresh as the hardcoded list. Warn ONCE
        # when operating past it — a silent gap in a safety net is the worst
        # kind of gap.
        if not self._stale_warned and ts > stale_after():
            self._stale_warned = True
            print(f"  [event calendar] WARNING: past last known OPEC date "
                  f"({stale_after():%Y-%m-%d}). OPEC meetings are NOT covered — "
                  f"refresh OPEC_DATES in research/event_calendar.py from opec.org.")

        for e in self._events_for_day(ts):
            if e.symbols and sym not in e.symbols:
                continue
            if e.severity == "medium" and not self.include_medium:
                continue
            if e.name.startswith("OPEC"):
                before, after = self.opec_before, self.opec_after
            else:
                before, after = self.before, self.after
            lo = e.when - pd.Timedelta(minutes=before)
            hi = e.when + pd.Timedelta(minutes=after)
            if lo <= ts <= hi:
                return True, f"{e.name} at {e.when:%Y-%m-%d %H:%M UTC}"
        return False, ""
