import unittest

from performance import calc_pnl_metrics, closed_trade_pnls


class PerformanceTests(unittest.TestCase):
    def test_closed_trade_pnls_filters_open_buys(self):
        trades = [
            {'side': 'BUY', 'pnl': 0.0},
            {'side': 'SELL_HALF', 'pnl': 120.0},
            {'side': 'SELL', 'pnl': -50.0},
        ]
        self.assertEqual(closed_trade_pnls(trades), [120.0, -50.0])

    def test_calc_pnl_metrics_basic_fields(self):
        metrics = calc_pnl_metrics([100.0, -50.0, 150.0], initial_cash=1000.0, n_periods=10)
        self.assertEqual(metrics['total_trades'], 3)
        self.assertAlmostEqual(metrics['win_rate'], 2 / 3)
        self.assertAlmostEqual(metrics['total_pnl'], 200.0)
        self.assertGreater(metrics['profit_factor'], 1.0)
        self.assertGreaterEqual(metrics['max_dd'], 0.0)


if __name__ == '__main__':
    unittest.main()
