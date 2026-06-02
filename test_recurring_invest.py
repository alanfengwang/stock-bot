import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from recurring_invest import should_execute_weekly_dca, week_marker


NY = ZoneInfo("America/New_York")


class RecurringInvestTests(unittest.TestCase):
    def test_week_marker_uses_iso_week(self):
        self.assertEqual(week_marker(datetime(2026, 6, 1, 10, 0, tzinfo=NY)), "2026-W23")

    def test_weekly_dca_waits_until_target_hour(self):
        now_et = datetime(2026, 6, 1, 9, 59, tzinfo=NY)
        self.assertFalse(should_execute_weekly_dca(now_et, "", weekday=0, min_hour=10))

    def test_weekly_dca_runs_once_when_window_opens(self):
        now_et = datetime(2026, 6, 1, 10, 0, tzinfo=NY)
        self.assertTrue(should_execute_weekly_dca(now_et, "2026-W22", weekday=0, min_hour=10))

    def test_weekly_dca_does_not_repeat_same_week(self):
        now_et = datetime(2026, 6, 1, 11, 0, tzinfo=NY)
        self.assertFalse(should_execute_weekly_dca(now_et, "2026-W23", weekday=0, min_hour=10))
