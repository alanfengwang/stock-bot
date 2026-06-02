"""
refresh_discussion_universe.py — 刷新当前讨论热度前 200 股票池

当前来源：
- ApeWisdom / r/wallstreetbets
- ApeWisdom / r/stocks
- 不足 200 时继续用 StockMarket / investing 补齐
"""

from __future__ import annotations

import requests

from discussion_universe import (
    fresh_metadata,
    parse_apewisdom_html,
    save_discussion_universe,
)


HEADERS = {'User-Agent': 'Mozilla/5.0'}
SOURCE_SPECS = [
    ('wallstreetbets', 'https://apewisdom.io/wallstreetbets/'),
    ('wallstreetbets', 'https://apewisdom.io/wallstreetbets/?page=2'),
    ('stocks', 'https://apewisdom.io/stocks/'),
    ('stockmarket', 'https://apewisdom.io/StockMarket/'),
    ('investing', 'https://apewisdom.io/investing/'),
]
TARGET_COUNT = 200


def main():
    ranked: list[dict] = []
    seen: set[str] = set()
    for board, url in SOURCE_SPECS:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        for item in parse_apewisdom_html(resp.text):
            code = item['code']
            if code in seen:
                continue
            seen.add(code)
            item['source_board'] = board
            ranked.append(item)
            if len(ranked) >= TARGET_COUNT:
                break
        if len(ranked) >= TARGET_COUNT:
            break

    ranked = ranked[:TARGET_COUNT]
    payload = fresh_metadata()
    payload['source']['boards'] = sorted({item['source_board'] for item in ranked})
    payload['items'] = ranked
    payload['summary'] = {
        'count': len(ranked),
        'top10': [item['symbol'] for item in ranked[:10]],
    }
    save_discussion_universe(payload)

    print(f"已刷新讨论热度股票池：{len(ranked)} 只")
    print("Top 10:", " ".join(item['symbol'] for item in ranked[:10]))


if __name__ == '__main__':
    main()
