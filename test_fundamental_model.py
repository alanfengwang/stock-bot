import unittest

from fundamental_model import score_slow_fundamentals


def make_entry(metrics: dict) -> dict:
    return {
        'status': 'ok',
        'source': 'sec',
        'updated_at': '2026-06-02',
        'metrics': metrics,
    }


class FundamentalModelTests(unittest.TestCase):
    def test_growth_software_scores_higher_with_strong_growth_and_cashflow(self):
        strong = make_entry({
            'revenue_annual': 10_000_000_000,
            'revenue_yoy': 0.28,
            'revenue_accel': 0.06,
            'gross_margin': 0.74,
            'operating_margin': 0.18,
            'operating_margin_delta': 0.03,
            'fcf_margin': 0.22,
            'total_cash': 4_000_000_000,
            'total_debt': 1_000_000_000,
            'net_debt': -3_000_000_000,
            'equity': 6_000_000_000,
            'roe': 0.24,
            'roic': 0.21,
            'free_cash_flow': 2_200_000_000,
            'net_income_annual': 1_500_000_000,
        })
        weak = make_entry({
            'revenue_annual': 10_000_000_000,
            'revenue_yoy': 0.02,
            'revenue_accel': -0.04,
            'gross_margin': 0.40,
            'operating_margin': -0.04,
            'operating_margin_delta': -0.02,
            'fcf_margin': -0.03,
            'total_cash': 500_000_000,
            'total_debt': 6_000_000_000,
            'net_debt': 5_500_000_000,
            'equity': 2_000_000_000,
            'roe': 0.03,
            'roic': 0.02,
            'free_cash_flow': -300_000_000,
            'net_income_annual': 100_000_000,
        })
        snapshot = {'pe_ttm_ratio': 45.0, 'pb_ratio': 8.0, 'total_market_val': 80_000_000_000}

        strong_score = score_slow_fundamentals('US.TEST', 'AI软件', strong, snapshot=snapshot)
        weak_score = score_slow_fundamentals('US.TEST', 'AI软件', weak, snapshot=snapshot)

        self.assertTrue(strong_score['available'])
        self.assertGreater(strong_score['score'], weak_score['score'])
        self.assertGreater(strong_score['score'], 70)

    def test_fintech_uses_sector_specific_template(self):
        entry = make_entry({
            'revenue_annual': 20_000_000_000,
            'revenue_yoy': 0.14,
            'revenue_accel': 0.02,
            'gross_margin': 0.55,
            'operating_margin': 0.24,
            'operating_margin_delta': 0.01,
            'fcf_margin': 0.18,
            'total_cash': 5_000_000_000,
            'total_debt': 3_000_000_000,
            'net_debt': -2_000_000_000,
            'equity': 10_000_000_000,
            'roe': 0.19,
            'roic': 0.15,
            'free_cash_flow': 3_600_000_000,
            'net_income_annual': 1_900_000_000,
        })
        snapshot = {'pe_ttm_ratio': 22.0, 'pb_ratio': 3.0, 'total_market_val': 90_000_000_000}
        score = score_slow_fundamentals('US.V', '金融科技', entry, snapshot=snapshot)
        self.assertTrue(score['available'])
        self.assertIn('valuation', score['components'])
        self.assertIn('balance', score['components'])
        self.assertGreater(score['score'], 60)

    def test_missing_entry_returns_unavailable(self):
        result = score_slow_fundamentals('US.NONE', 'AI软件', None, snapshot={})
        self.assertFalse(result['available'])
        self.assertIsNone(result['score'])

    def test_etf_uses_passive_template_without_sec_entry(self):
        result = score_slow_fundamentals('US.QQQ', '指数ETF', None, snapshot={})
        self.assertTrue(result['available'])
        self.assertEqual(result['score'], 70.0)


if __name__ == '__main__':
    unittest.main()
