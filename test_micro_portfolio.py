import unittest

from micro_portfolio import select_diversified_micro_candidates


class MicroPortfolioTests(unittest.TestCase):
    def test_sector_diversification_respects_cap(self):
        universe = {
            '科技': ['US.A', 'US.B'],
            '金融': ['US.C', 'US.D'],
            '医疗': ['US.E'],
        }
        scores = {
            'US.A': 8.5,
            'US.B': 8.2,
            'US.C': 7.6,
            'US.D': 7.0,
            'US.E': 7.8,
        }

        picks = select_diversified_micro_candidates(
            scores,
            universe,
            held=set(),
            target_positions=3,
            max_positions=5,
            sector_cap=1,
            min_score=6.5,
        )

        self.assertEqual([row['code'] for row in picks], ['US.A', 'US.E', 'US.C'])

    def test_selection_skips_held_and_low_score_names(self):
        universe = {
            '科技': ['US.A', 'US.B'],
            '金融': ['US.C'],
        }
        scores = {
            'US.A': 8.5,
            'US.B': 6.0,
            'US.C': 7.4,
        }

        picks = select_diversified_micro_candidates(
            scores,
            universe,
            held={'US.A'},
            target_positions=3,
            max_positions=3,
            sector_cap=1,
            min_score=6.5,
        )

        self.assertEqual([row['code'] for row in picks], ['US.C'])

    def test_required_sector_and_override_cap(self):
        universe = {
            'AI软件/云': ['US.A', 'US.B'],
            '太空国防': ['US.C'],
            '金融': ['US.D'],
        }
        scores = {
            'US.A': 8.2,
            'US.B': 7.6,
            'US.C': 6.8,
            'US.D': 7.4,
        }

        picks = select_diversified_micro_candidates(
            scores,
            universe,
            held=set(),
            target_positions=4,
            max_positions=4,
            sector_cap=1,
            min_score=6.5,
            sector_caps={'AI软件/云': 2},
            required_sectors=('太空国防',),
        )

        self.assertEqual([row['code'] for row in picks], ['US.C', 'US.A', 'US.D', 'US.B'])


if __name__ == '__main__':
    unittest.main()
