import unittest

from trade_costs import apply_slippage, calc_commission


class TradeCostTests(unittest.TestCase):
    def test_commission_uses_minimum(self):
        self.assertEqual(calc_commission(10.0, 10), 1.0)

    def test_commission_scales_for_large_orders(self):
        self.assertAlmostEqual(calc_commission(100.0, 1000), 30.0)

    def test_buy_slippage_moves_price_up(self):
        self.assertAlmostEqual(apply_slippage(100.0, 'BUY', 10), 100.1)

    def test_sell_slippage_moves_price_down(self):
        self.assertAlmostEqual(apply_slippage(100.0, 'SELL', 10), 99.9)


if __name__ == '__main__':
    unittest.main()
