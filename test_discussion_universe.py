import unittest

from discussion_universe import build_watch_universe, discussion_codes, parse_apewisdom_html


SAMPLE_HTML = """
<table>
  <tbody>
    <tr>
      <td class="fav"></td>
      <td class="td-right" data-sort="1">1</td>
      <td class="name-td">
        <a href="/stocks/NVDA/">
          <div class="name-div">
            <div class="company-name">NVIDIA</div>
          </div>
        </a>
      </td>
      <td class="td-right"><span class="badge badge-company">NVDA</span></td>
      <td class="td-center rh-sm" data-sort="218">218</td>
      <td class="td-center rh-sm" data-sort="432.0">432%</td>
      <td class="p-0 sparkline-td green"></td>
      <td class="td-right" data-sort="1819">1819</td>
    </tr>
    <tr>
      <td class="fav"></td>
      <td class="td-right" data-sort="2">2</td>
      <td class="name-td">
        <a href="/stocks/MU/">
          <div class="name-div">
            <div class="company-name">Micron Technology</div>
          </div>
        </a>
      </td>
      <td class="td-right"><span class="badge badge-company">MU</span></td>
      <td class="td-center rh-sm" data-sort="100">100</td>
      <td class="td-center rh-sm" data-sort="">N/A</td>
      <td class="p-0 sparkline-td green"></td>
      <td class="td-right" data-sort="900">900</td>
    </tr>
  </tbody>
</table>
"""


class DiscussionUniverseTests(unittest.TestCase):
    def test_parse_apewisdom_html(self):
        items = parse_apewisdom_html(SAMPLE_HTML)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['symbol'], 'NVDA')
        self.assertEqual(items[0]['code'], 'US.NVDA')
        self.assertEqual(items[1]['mentions_change_24h_pct'], None)

    def test_discussion_codes(self):
        codes = discussion_codes({'items': parse_apewisdom_html(SAMPLE_HTML)}, limit=1)
        self.assertEqual(codes, ['US.NVDA'])

    def test_build_watch_universe_appends_only_new_discussion_codes(self):
        payload = {
            'items': [
                {'symbol': 'NVDA'},
                {'symbol': 'AAPL'},
                {'symbol': 'MU'},
                {'symbol': 'MSFT'},
            ]
        }
        merged = build_watch_universe(['US.NVDA', 'US.MU'], payload, extra_limit=2)
        self.assertEqual(merged, ['US.NVDA', 'US.MU', 'US.AAPL', 'US.MSFT'])


if __name__ == '__main__':
    unittest.main()
