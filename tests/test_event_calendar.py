"""The event blackout the live bots use (strategies/news_filter.py).

Times are US/Eastern. Run from the repository root:
    python -m unittest discover -s tests
"""
import unittest
from datetime import date
from unittest import mock

import pandas as pd

from research import event_calendar as ec

ET = "America/New_York"


def et(s: str) -> pd.Timestamp:
    return pd.Timestamp(s).tz_localize(ET)


class TradingCalendar(unittest.TestCase):
    def test_nyse_closures_are_not_the_federal_list(self):
        self.assertFalse(ec.is_trading_day(date(2026, 7, 3)))    # July 4 observed
        self.assertFalse(ec.is_trading_day(date(2026, 4, 3)))    # Good Friday
        self.assertTrue(ec.is_trading_day(date(2026, 10, 12)))   # Columbus Day
        self.assertTrue(ec.is_trading_day(date(2026, 11, 11)))   # Veterans Day
        self.assertFalse(ec.is_trading_day(date(2026, 10, 4)))   # Sunday

    def test_next_session_open_skips_weekends_and_holidays(self):
        self.assertEqual(ec.next_session_open(date(2026, 10, 4)), et("2026-10-05 09:30"))
        self.assertEqual(ec.next_session_open(date(2026, 9, 6)), et("2026-09-08 09:30"))
        self.assertEqual(ec.next_session_open(date(2026, 7, 2)), et("2026-07-06 09:30"))


class SundayOpecMeeting(unittest.TestCase):
    """OPEC+ meets on Sundays. The blackout has to reach the first session."""

    def setUp(self):
        self.cal = ec.EventCalendar()

    def blocked(self, when: str, symbol: str = "USO", cal=None) -> bool:
        return (cal or self.cal).blackout(et(when), symbol)[0]

    def test_blocks_the_first_hours_of_monday(self):
        self.assertTrue(self.blocked("2026-10-05 09:35"))
        self.assertTrue(self.blocked("2026-10-05 13:25"))
        self.assertFalse(self.blocked("2026-10-05 13:35"))

    def test_old_window_never_reached_a_session(self):
        old = ec.EventCalendar(opec_next_session=False)
        self.assertTrue(self.blocked("2026-10-04 08:00", cal=old))
        self.assertFalse(self.blocked("2026-10-05 09:35", cal=old))

    def test_holiday_monday_moves_it_to_tuesday(self):
        # 6 Sep 2026 meeting; Monday 7 Sep is Labor Day.
        self.assertTrue(self.blocked("2026-09-08 09:35"))
        self.assertFalse(self.blocked("2026-09-08 13:35"))

    def test_covers_the_sunday_evening_futures_open(self):
        self.assertTrue(self.blocked("2026-10-04 18:05", symbol="CL"))

    def test_only_crude_symbols(self):
        self.assertFalse(self.blocked("2026-10-05 09:35", symbol="SPY"))

    def test_event_names_the_session(self):
        ev = [e for e in ec.events_between(et("2026-10-04 00:00"), et("2026-10-04 23:59"))
              if e.name.startswith("OPEC")]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0].next_open, et("2026-10-05 09:30"))
        self.assertIn("Mon Oct 05 09:30", ev[0].name)


class WeekdayOpecMeeting(unittest.TestCase):
    def test_keeps_the_meeting_day_window(self):
        with mock.patch.object(ec, "OPEC_DATES", ["2026-10-07"]):   # a Wednesday
            cal = ec.EventCalendar()
            self.assertTrue(cal.blackout(et("2026-10-07 11:55"), "USO")[0])
            self.assertFalse(cal.blackout(et("2026-10-07 12:05"), "USO")[0])
            ev = [e for e in ec.events_between(et("2026-10-07 00:00"), et("2026-10-07 23:59"))
                  if e.name.startswith("OPEC")]
            self.assertIsNone(ev[0].next_open)


class EiaReport(unittest.TestCase):
    def test_wednesday_unless_a_holiday_earlier_in_the_week(self):
        self.assertEqual(ec.eia_petroleum_day(date(2026, 10, 5)), date(2026, 10, 7))
        self.assertEqual(ec.eia_petroleum_day(date(2026, 11, 9)), date(2026, 11, 12))


if __name__ == "__main__":
    unittest.main()
