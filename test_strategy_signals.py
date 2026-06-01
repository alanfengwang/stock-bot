import unittest

import pandas as pd

from strategy_signals import detect_entry_signal, indicator_state


class StrategySignalTests(unittest.TestCase):
    def test_conservative_indicator_state_uses_rsi_thresholds(self):
        latest = pd.Series({'rsi': 38.0})
        prev = pd.Series({'rsi': 45.0})
        cfg = {'rsi_buy': 40, 'rsi_sell': 75, 'add_rsi_max': 65}
        state = indicator_state('conservative', cfg, latest, prev)
        self.assertTrue(state.extra_buy)
        self.assertFalse(state.extra_sell)
        self.assertTrue(state.relaxed_buy)

    def test_detects_trend_pullback(self):
        history = pd.DataFrame({
            'close': [100.0, 103.0, 105.0, 102.0, 106.0],
            'low': [99.0, 102.0, 104.0, 100.0, 105.0],
            'high': [101.0, 104.0, 106.0, 103.0, 107.0],
            'volume': [100, 110, 120, 115, 140],
        })
        latest = history.iloc[-1]
        prev = history.iloc[-2]
        cfg = {
            'entry_modes': ('trend_pullback',),
            'fast_ma': 5,
            'pullback_lookback': 3,
            'pullback_band': 0.02,
            'pullback_reclaim_tol': 0.02,
        }
        signal = detect_entry_signal(
            cfg, history, latest, prev,
            fast_now=104.0, slow_now=101.0,
            fast_prev=103.0, slow_prev=101.0,
            signal_ok=True,
        )
        self.assertEqual(signal[0], 'trend_pullback')

    def test_detects_breakout(self):
        history = pd.DataFrame({
            'close': [10.0, 10.1, 10.2, 10.2, 10.3, 10.4, 10.9],
            'low': [9.9, 10.0, 10.1, 10.1, 10.2, 10.3, 10.8],
            'high': [10.1, 10.2, 10.3, 10.3, 10.35, 10.45, 10.95],
            'volume': [100, 100, 100, 100, 100, 100, 300],
        })
        latest = history.iloc[-1]
        prev = history.iloc[-2]
        cfg = {
            'entry_modes': ('breakout',),
            'breakout_lookback': 5,
            'breakout_buffer': 0.0,
            'breakout_vol_mult': 1.2,
            'vol_period': 3,
        }
        signal = detect_entry_signal(
            cfg, history, latest, prev,
            fast_now=10.8, slow_now=10.4,
            fast_prev=10.5, slow_prev=10.3,
            signal_ok=True,
        )
        self.assertEqual(signal[0], 'breakout')


if __name__ == '__main__':
    unittest.main()
