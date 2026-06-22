from __future__ import annotations

import unittest
from unittest.mock import patch

from entry_engine import score_entry_candidate, signal_threshold_for_quality


class EntryEngineTests(unittest.TestCase):
    def test_signal_threshold_relaxes_for_high_quality(self):
        self.assertEqual(signal_threshold_for_quality(10.0, None), 10.0)
        self.assertEqual(signal_threshold_for_quality(10.0, 5.5), 10.0)
        self.assertEqual(signal_threshold_for_quality(10.0, 6.2), 9.0)
        self.assertEqual(signal_threshold_for_quality(10.0, 7.8), 8.0)

    @patch('entry_engine.get_regime', return_value='BULL')
    def test_strong_candidate_gets_full_size(self, _mock_regime):
        result = score_entry_candidate(
            bucket_name='longterm',
            signal_score=14.0,
            quality_score=7.8,
            fund_sc=6.0,
            skip_fast_fund_gate=True,
            vol_sig='positive',
            emotion_top=False,
        )
        self.assertEqual(result.action, 'full')
        self.assertGreaterEqual(result.total, 72.0)

    @patch('entry_engine.get_regime', return_value='NEUTRAL')
    def test_borderline_candidate_can_fall_back_to_probe(self, _mock_regime):
        result = score_entry_candidate(
            bucket_name='longterm',
            signal_score=10.0,
            quality_score=5.4,
            fund_sc=4.5,
            skip_fast_fund_gate=True,
            vol_sig='neutral',
            emotion_top=False,
        )
        self.assertEqual(result.action, 'probe')
        self.assertGreaterEqual(result.total, 48.0)
        self.assertLess(result.total, 60.0)

    @patch('entry_engine.get_regime', return_value='NEUTRAL')
    def test_very_weak_fast_fund_is_still_hard_reject(self, _mock_regime):
        result = score_entry_candidate(
            bucket_name='longterm',
            signal_score=14.0,
            quality_score=6.5,
            fund_sc=1.0,
            skip_fast_fund_gate=False,
            vol_sig='positive',
            emotion_top=False,
        )
        self.assertEqual(result.action, 'reject')
        self.assertEqual(result.total, 0.0)


if __name__ == '__main__':
    unittest.main()
