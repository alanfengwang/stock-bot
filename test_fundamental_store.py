import json
import os
import tempfile
import unittest
from unittest.mock import patch

import fundamental_store


class FundamentalStoreTests(unittest.TestCase):
    def test_load_legacy_flat_cache_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, 'fundamental_cache.json')
            with open(cache_path, 'w') as f:
                json.dump({
                    'US.AAPL': {'status': 'ok', 'updated_at': '2026-06-01'},
                    'US.MSFT': {'status': 'missing', 'updated_at': '2026-06-01'},
                }, f)

            with patch.object(fundamental_store, 'FUNDAMENTAL_CACHE_PATH', cache_path):
                cache = fundamental_store.load_fundamental_cache()

            self.assertEqual(set(cache.keys()), {'US.AAPL', 'US.MSFT'})
            self.assertEqual(cache['US.AAPL']['status'], 'ok')

    def test_save_and_load_wrapped_cache_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, 'fundamental_cache.json')
            payload = {
                'US.NVDA': {'status': 'ok', 'updated_at': '2026-06-02'},
            }

            with patch.object(fundamental_store, 'FUNDAMENTAL_CACHE_PATH', cache_path):
                fundamental_store.save_fundamental_cache(payload)
                with open(cache_path) as f:
                    raw = json.load(f)
                loaded = fundamental_store.load_fundamental_cache()

            self.assertIn('entries', raw)
            self.assertEqual(raw['entries']['US.NVDA']['status'], 'ok')
            self.assertEqual(loaded['US.NVDA']['status'], 'ok')


if __name__ == '__main__':
    unittest.main()
