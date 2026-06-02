import unittest
from datetime import datetime

from exit_rules import evaluate_trailing_stop, holding_days, should_time_stop


class ExitRulesTests(unittest.TestCase):
    def test_holding_days_parses_entry_time(self):
        now = datetime(2026, 6, 5, 10, 0, 0)
        self.assertEqual(holding_days('2026-06-01 09:30:00', now=now), 4)

    def test_trailing_stop_only_triggers_after_activation(self):
        state = evaluate_trailing_stop(
            entry_price=100.0,
            current_price=101.0,
            trail_high=102.0,
            atr_value=2.0,
            activate_profit=0.03,
            break_even_profit=0.05,
            atr_mult=1.5,
        )
        self.assertFalse(state['trail_active'])
        self.assertFalse(state['triggered'])

    def test_break_even_floor_applies_after_larger_profit(self):
        state = evaluate_trailing_stop(
            entry_price=100.0,
            current_price=100.2,
            trail_high=106.0,
            atr_value=4.0,
            activate_profit=0.02,
            break_even_profit=0.04,
            break_even_buffer=0.001,
            atr_mult=1.5,
        )
        self.assertTrue(state['trail_active'])
        self.assertTrue(state['break_even_active'])
        self.assertGreaterEqual(state['effective_stop'], 100.1)

    def test_time_stop_requires_age_and_underperformance(self):
        now = datetime(2026, 6, 5, 10, 0, 0)
        self.assertTrue(
            should_time_stop(
                '2026-06-01 09:30:00',
                pnl_pct=0.01,
                max_days=3,
                min_return=0.02,
                now=now,
            )
        )
        self.assertFalse(
            should_time_stop(
                '2026-06-01 09:30:00',
                pnl_pct=0.03,
                max_days=3,
                min_return=0.02,
                now=now,
            )
        )


if __name__ == '__main__':
    unittest.main()
