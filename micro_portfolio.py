"""
micro_portfolio.py — 分散式微底仓选择逻辑
"""

from __future__ import annotations

import pandas as pd


def score_snapshot_row(row: pd.Series) -> float:
    """综合评分 0-10：基本面(50%) + 52周位置(25%) + 今日动量(25%)"""
    eps = float(row.get('earning_per_share') or 0)
    pe = float(row.get('pe_ttm_ratio') or 0)
    cap = float(row.get('total_market_val') or 0)
    h52 = float(row.get('highest52weeks_price') or 0)
    l52 = float(row.get('lowest52weeks_price') or 0)

    overnight = float(row.get('overnight_price') or 0)
    pre = float(row.get('pre_price') or 0)
    after = float(row.get('after_price') or 0)
    last = float(row.get('last_price') or 0)
    live = overnight or pre or after or last
    prev = float(row.get('prev_close_price') or live)

    if eps <= 0:
        return 1.0

    fund = 5.0
    if cap > 5e11:
        fund += 2.0
    elif cap > 1e11:
        fund += 1.0
    elif cap < 1e10:
        fund -= 1.0

    if 0 < pe <= 15:
        fund += 2.0
    elif 15 < pe <= 30:
        fund += 1.5
    elif 30 < pe <= 60:
        fund += 0.5
    elif pe > 100:
        fund -= 1.5
    fund = min(10.0, max(0.0, fund))

    w52 = (live - l52) / (h52 - l52) * 100 if h52 > l52 else 50
    pos_sc = max(0.0, 10.0 - abs(w52 - 50) * 0.15)

    d_chg = (live - prev) / prev * 100 if prev else 0
    mom_sc = max(0.0, min(10.0, 5.0 + d_chg * 0.5))

    return round(fund * 0.50 + pos_sc * 0.25 + mom_sc * 0.25, 2)


def select_diversified_micro_candidates(
    scores: dict[str, float],
    universe: dict[str, list[str]],
    held: set[str],
    target_positions: int,
    max_positions: int,
    sector_cap: int,
    min_score: float,
    sector_caps: dict[str, int] | None = None,
    required_sectors: tuple[str, ...] | list[str] | None = None,
) -> list[dict]:
    """
    先保证行业覆盖，再按分数补充。

    返回：
      [{'sector': 'AI芯片', 'code': 'US.AMD', 'score': 7.3}, ...]
    """
    if target_positions <= 0 or max_positions <= 0:
        return []

    sector_caps = sector_caps or {}
    required_sectors = tuple(required_sectors or ())

    def cap_for(sector: str) -> int:
        return max(0, int(sector_caps.get(sector, sector_cap)))

    candidates_by_sector: dict[str, list[tuple[str, float]]] = {}
    for sector, stocks in universe.items():
        rows = [
            (code, scores.get(code, 0.0))
            for code in stocks
            if code not in held and scores.get(code, 0.0) >= min_score
        ]
        rows.sort(key=lambda x: x[1], reverse=True)
        candidates_by_sector[sector] = rows

    picks: list[dict] = []
    picked_codes: set[str] = set()
    sector_counts = {sector: 0 for sector in universe}
    rank = 0

    target_cap = min(target_positions, max_positions)

    for sector in required_sectors:
        if len(picks) >= target_cap:
            break
        rows = candidates_by_sector.get(sector, [])
        if not rows or cap_for(sector) <= 0:
            continue
        code, score = rows[0]
        if code in picked_codes:
            continue
        picks.append({'sector': sector, 'code': code, 'score': score})
        picked_codes.add(code)
        sector_counts[sector] += 1

    while len(picks) < target_cap:
        round_rows: list[dict] = []
        for sector, rows in candidates_by_sector.items():
            if sector_counts[sector] >= cap_for(sector):
                continue
            if rank >= len(rows):
                continue
            code, score = rows[rank]
            if code in picked_codes:
                continue
            round_rows.append({'sector': sector, 'code': code, 'score': score})

        if not round_rows:
            break

        round_rows.sort(key=lambda x: x['score'], reverse=True)
        for row in round_rows:
            if len(picks) >= target_cap:
                break
            if sector_counts[row['sector']] >= cap_for(row['sector']):
                continue
            if row['code'] in picked_codes:
                continue
            picks.append(row)
            picked_codes.add(row['code'])
            sector_counts[row['sector']] += 1
        rank += 1

    return picks[:max_positions]
