import sys
import types
import unittest


_moomoo_mock = types.ModuleType('moomoo')
_moomoo_mock.KLType = type(
    'KLType',
    (),
    {'K_DAY': 'K_DAY', 'K_5M': 'K_5M', 'K_15M': 'K_15M', 'K_60M': 'K_60M', 'K_WEEK': 'K_WEEK'},
)()
sys.modules.setdefault('moomoo', _moomoo_mock)

from strategy_config import TRADE_UNIVERSE, WATCH_UNIVERSE, bucket_stocks


class StrategyUniverseTests(unittest.TestCase):
    def test_watch_universe_is_larger_than_trade_universe(self):
        self.assertGreater(len(WATCH_UNIVERSE), len(TRADE_UNIVERSE))
        self.assertGreaterEqual(len(WATCH_UNIVERSE), 150)

    def test_trade_universe_is_subset_of_watch_universe(self):
        self.assertTrue(set(TRADE_UNIVERSE).issubset(set(WATCH_UNIVERSE)))

    def test_bucket_stocks_are_in_trade_universe(self):
        self.assertTrue(set(bucket_stocks()).issubset(set(TRADE_UNIVERSE)))


if __name__ == '__main__':
    unittest.main()
