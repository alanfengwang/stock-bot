import os
import tempfile
import unittest

from local_broker import LocalBroker


class LocalBrokerTests(unittest.TestCase):
    def test_marker_persists_across_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "account.json")
            log_path = os.path.join(tmp, "trade_log.csv")

            broker = LocalBroker(db_path, log_path, initial_cash=1000.0)
            self.assertEqual(broker.get_marker("weekly_dca:US.QQQ"), "")

            broker.set_marker("weekly_dca:US.QQQ", "2026-W23")

            broker2 = LocalBroker(db_path, log_path, initial_cash=1000.0)
            self.assertEqual(broker2.get_marker("weekly_dca:US.QQQ"), "2026-W23")

    def test_sell_sets_reentry_cooldown_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "account.json")
            log_path = os.path.join(tmp, "trade_log.csv")

            broker = LocalBroker(db_path, log_path, initial_cash=1000.0)
            ok, _ = broker.place_order("US.NOW", "BUY", 2, 100.0, bucket="micro", reason="micro_position")
            self.assertTrue(ok)
            ok, _ = broker.place_order("US.NOW", "SELL", 2, 101.0, bucket="micro", reason="trailing_stop")
            self.assertTrue(ok)

            self.assertEqual(broker.last_sell_reason("US.NOW"), "trailing_stop")
            self.assertNotEqual(broker.get_marker("last_sell_ts:US.NOW", ""), "")
            self.assertTrue(broker.was_sold_recently("US.NOW", 60))
