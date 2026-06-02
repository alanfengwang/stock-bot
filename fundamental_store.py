"""
fundamental_store.py — 日更慢速基本面缓存

数据源：
1. SEC company_tickers.json
2. SEC companyfacts JSON

目标：
- 每日更新一次 watch universe 的慢速基本面数据
- 供 fundamental_model.py 和 dashboard / bot 读取
"""

from __future__ import annotations

from datetime import date, datetime
import json
import os
import time
from typing import Any

import requests

BASE = os.path.dirname(__file__)
FUNDAMENTAL_CACHE_PATH = os.path.join(BASE, 'fundamental_cache.json')
SEC_TICKER_MAP_PATH = os.path.join(BASE, 'sec_ticker_map.json')

SEC_HEADERS = {
    'User-Agent': os.getenv(
        'SEC_USER_AGENT',
        'StockResearchBot/1.0 stock-research@example.com',
    ),
    'Accept-Encoding': 'gzip, deflate',
}
FUNDAMENTAL_TTL_DAYS = 1
TICKER_MAP_TTL_DAYS = 30
SEC_REQUEST_SLEEP = 0.15


def normalize_code(code: str) -> str:
    code = str(code).strip().upper()
    return code if code.startswith('US.') else f'US.{code}'


def normalize_ticker(code: str) -> str:
    return normalize_code(code).split('.', 1)[1]


def _read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def _write_json(path: str, payload: Any):
    with open(path, 'w') as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def _today_str() -> str:
    return date.today().isoformat()


def _is_stale(updated_at: str | None, ttl_days: int) -> bool:
    if not updated_at:
        return True
    try:
        updated = datetime.fromisoformat(updated_at).date()
    except ValueError:
        return True
    return (date.today() - updated).days >= ttl_days


def load_fundamental_cache() -> dict[str, dict]:
    payload = _read_json(FUNDAMENTAL_CACHE_PATH, {})
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get('entries'), dict):
        payload = payload['entries']
    # 兼容旧版平铺格式：{ "US.AAPL": {...}, ... }
    return {
        normalize_code(code): entry
        for code, entry in payload.items()
        if isinstance(entry, dict) and str(code).upper().startswith('US.')
    }


def save_fundamental_cache(cache: dict[str, dict]):
    _write_json(FUNDAMENTAL_CACHE_PATH, {
        'updated_at': _today_str(),
        'entries': cache,
    })


def _fetch_json(url: str) -> dict:
    resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def load_sec_ticker_map(force_refresh: bool = False) -> dict[str, int]:
    cache = _read_json(SEC_TICKER_MAP_PATH, {})
    meta = cache.get('_meta', {}) if isinstance(cache, dict) else {}
    mapping = cache.get('mapping', {}) if isinstance(cache, dict) else {}
    if not force_refresh and mapping and not _is_stale(meta.get('updated_at'), TICKER_MAP_TTL_DAYS):
        return {str(k): int(v) for k, v in mapping.items()}

    payload = _fetch_json('https://www.sec.gov/files/company_tickers.json')
    fresh = {
        str(item['ticker']).upper(): int(item['cik_str'])
        for item in payload.values()
    }
    _write_json(SEC_TICKER_MAP_PATH, {
        '_meta': {'updated_at': _today_str()},
        'mapping': fresh,
    })
    return fresh


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d')
    except ValueError:
        return None


def _metric_records(companyfacts: dict, metric: str) -> list[dict]:
    facts = companyfacts.get('facts', {}).get('us-gaap', {}).get(metric, {})
    units = facts.get('units', {})
    records: list[dict] = []
    for unit, entries in units.items():
        if not str(unit).startswith('USD'):
            continue
        records.extend(entries)
    return records


def _duration_series(
    companyfacts: dict,
    aliases: list[str],
    min_days: int,
    max_days: int,
) -> list[dict]:
    best_by_end: dict[str, dict] = {}
    for alias in aliases:
        for record in _metric_records(companyfacts, alias):
            start = _parse_date(record.get('start'))
            end = _parse_date(record.get('end'))
            if start is None or end is None:
                continue
            days = (end - start).days + 1
            if days < min_days or days > max_days:
                continue
            form = str(record.get('form', ''))
            if form not in {'10-Q', '10-Q/A', '10-K', '10-K/A', '20-F', '20-F/A'}:
                continue
            try:
                val = float(record['val'])
            except (KeyError, TypeError, ValueError):
                continue
            end_key = end.date().isoformat()
            filed = record.get('filed', '')
            cur = best_by_end.get(end_key)
            if cur is None or str(filed) > str(cur.get('filed', '')):
                best_by_end[end_key] = {
                    'end': end_key,
                    'value': val,
                    'filed': filed,
                    'alias': alias,
                }
    return sorted(best_by_end.values(), key=lambda row: row['end'])


def _latest_instant_value(companyfacts: dict, aliases: list[str]) -> float | None:
    best: dict | None = None
    for alias in aliases:
        for record in _metric_records(companyfacts, alias):
            end = _parse_date(record.get('end'))
            if end is None:
                continue
            try:
                val = float(record['val'])
            except (KeyError, TypeError, ValueError):
                continue
            filed = record.get('filed', '')
            row = {'end': end.date().isoformat(), 'filed': filed, 'value': val}
            if best is None or row['end'] > best['end'] or (
                row['end'] == best['end'] and row['filed'] > best['filed']
            ):
                best = row
    return None if best is None else float(best['value'])


def _sum_instant_values(companyfacts: dict, alias_groups: list[list[str]]) -> float | None:
    parts: list[float] = []
    for aliases in alias_groups:
        val = _latest_instant_value(companyfacts, aliases)
        if val is not None:
            parts.append(float(val))
    if parts:
        return sum(parts)
    return None


def _first_available_value(companyfacts: dict, alias_groups: list[list[str]]) -> float | None:
    for aliases in alias_groups:
        val = _latest_instant_value(companyfacts, aliases)
        if val is not None:
            return val
    return None


def _safe_ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return float(num) / float(den)


def _annual_value(companyfacts: dict, aliases: list[str]) -> float | None:
    annual = _duration_series(companyfacts, aliases, 300, 380)
    if not annual:
        return None
    return float(annual[-1]['value'])


def _quarterly_series(companyfacts: dict, aliases: list[str]) -> list[float]:
    series = _duration_series(companyfacts, aliases, 75, 110)
    return [float(item['value']) for item in series]


def _quarterly_yoy(series: list[float]) -> tuple[float | None, float | None]:
    if len(series) >= 5 and series[-5] != 0:
        current = series[-1] / series[-5] - 1
    else:
        current = None
    if len(series) >= 6 and series[-6] != 0:
        previous = series[-2] / series[-6] - 1
    else:
        previous = None
    return current, previous


def fetch_sec_fundamentals(code: str, cik_map: dict[str, int] | None = None) -> dict:
    code = normalize_code(code)
    ticker = normalize_ticker(code)
    cik_map = cik_map or load_sec_ticker_map()
    cik = cik_map.get(ticker)
    if cik is None:
        return {
            'code': code,
            'ticker': ticker,
            'status': 'missing',
            'updated_at': _today_str(),
            'reason': 'ticker_not_in_sec_map',
        }

    companyfacts = _fetch_json(
        f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json'
    )

    revenue_aliases = [
        'Revenues',
        'RevenueFromContractWithCustomerExcludingAssessedTax',
        'RevenueFromContractWithCustomerIncludingAssessedTax',
        'SalesRevenueNet',
        'SalesRevenueServicesNet',
    ]
    gross_profit_aliases = ['GrossProfit']
    operating_income_aliases = ['OperatingIncomeLoss']
    net_income_aliases = ['NetIncomeLoss', 'ProfitLoss']
    cfo_aliases = [
        'NetCashProvidedByUsedInOperatingActivities',
        'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations',
    ]
    capex_aliases = [
        'PaymentsToAcquirePropertyPlantAndEquipment',
        'CapitalExpendituresIncurredButNotYetPaid',
        'PropertyPlantAndEquipmentAdditions',
    ]
    cash_aliases = [
        'CashAndCashEquivalentsAtCarryingValue',
        'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents',
    ]
    equity_aliases = [
        'StockholdersEquity',
        'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
    ]

    revenue_annual = _annual_value(companyfacts, revenue_aliases)
    gross_profit_annual = _annual_value(companyfacts, gross_profit_aliases)
    operating_income_annual = _annual_value(companyfacts, operating_income_aliases)
    net_income_annual = _annual_value(companyfacts, net_income_aliases)
    cfo_annual = _annual_value(companyfacts, cfo_aliases)
    capex_annual = _annual_value(companyfacts, capex_aliases)

    revenue_q = _quarterly_series(companyfacts, revenue_aliases)
    operating_income_q = _quarterly_series(companyfacts, operating_income_aliases)
    gross_profit_q = _quarterly_series(companyfacts, gross_profit_aliases)

    revenue_yoy, previous_revenue_yoy = _quarterly_yoy(revenue_q)
    revenue_accel = (
        None if revenue_yoy is None or previous_revenue_yoy is None
        else revenue_yoy - previous_revenue_yoy
    )

    q_gross_margin = None
    q_operating_margin = None
    q_operating_margin_prev = None
    if gross_profit_q and revenue_q and len(gross_profit_q) == len(revenue_q):
        q_gross_margin = _safe_ratio(gross_profit_q[-1], revenue_q[-1])
    if operating_income_q and revenue_q and len(operating_income_q) == len(revenue_q):
        q_operating_margin = _safe_ratio(operating_income_q[-1], revenue_q[-1])
        if len(operating_income_q) >= 2 and len(revenue_q) >= 2:
            q_operating_margin_prev = _safe_ratio(operating_income_q[-2], revenue_q[-2])

    total_cash = _latest_instant_value(companyfacts, cash_aliases)
    debt_noncurrent = _first_available_value(companyfacts, [
        ['LongTermDebtNoncurrent'],
        ['LongTermDebtAndCapitalLeaseObligationsNoncurrent'],
        ['LongTermDebtAndCapitalLeaseObligations'],
        ['LongTermDebt'],
    ])
    debt_current = _first_available_value(companyfacts, [
        ['LongTermDebtCurrent'],
        ['LongTermDebtAndCapitalLeaseObligationsCurrent'],
        ['ShortTermBorrowings'],
        ['CommercialPaper'],
    ])
    total_debt = (
        (debt_noncurrent or 0.0) + (debt_current or 0.0)
        if debt_noncurrent is not None or debt_current is not None
        else None
    )
    equity = _latest_instant_value(companyfacts, equity_aliases)
    net_debt = (
        None if total_debt is None and total_cash is None
        else float(total_debt or 0.0) - float(total_cash or 0.0)
    )

    gross_margin = _safe_ratio(gross_profit_annual, revenue_annual)
    operating_margin = _safe_ratio(operating_income_annual, revenue_annual)
    capex_abs = abs(capex_annual) if capex_annual is not None else None
    free_cash_flow = None if cfo_annual is None or capex_abs is None else cfo_annual - capex_abs
    fcf_margin = _safe_ratio(free_cash_flow, revenue_annual)
    roe = _safe_ratio(net_income_annual, equity)

    roic = None
    if operating_income_annual is not None and equity is not None:
        invested_capital = float(equity) + float(total_debt or 0.0) - float(total_cash or 0.0)
        if invested_capital > 0:
            nopat = operating_income_annual * 0.79 if operating_income_annual > 0 else operating_income_annual
            roic = nopat / invested_capital

    operating_margin_delta = (
        None if q_operating_margin is None or q_operating_margin_prev is None
        else q_operating_margin - q_operating_margin_prev
    )

    return {
        'code': code,
        'ticker': ticker,
        'entity_name': companyfacts.get('entityName', ticker),
        'status': 'ok',
        'source': 'sec',
        'updated_at': _today_str(),
        'metrics': {
            'revenue_annual': revenue_annual,
            'revenue_yoy': revenue_yoy,
            'revenue_accel': revenue_accel,
            'gross_margin': gross_margin if gross_margin is not None else q_gross_margin,
            'operating_margin': operating_margin if operating_margin is not None else q_operating_margin,
            'operating_margin_delta': operating_margin_delta,
            'free_cash_flow': free_cash_flow,
            'fcf_margin': fcf_margin,
            'total_cash': total_cash,
            'total_debt': total_debt,
            'net_debt': net_debt,
            'equity': equity,
            'roe': roe,
            'roic': roic,
            'net_income_annual': net_income_annual,
        },
    }


def get_fundamental_entry(code: str, refresh_if_stale: bool = False) -> dict:
    code = normalize_code(code)
    cache = load_fundamental_cache()
    entry = cache.get(code)
    if entry and (not refresh_if_stale or not _is_stale(entry.get('updated_at'), FUNDAMENTAL_TTL_DAYS)):
        return entry
    if refresh_if_stale:
        fresh = fetch_sec_fundamentals(code)
        cache[code] = fresh
        save_fundamental_cache(cache)
        return fresh
    return entry or {}


def refresh_fundamental_cache(
    codes: list[str],
    force: bool = False,
    sleep_seconds: float = SEC_REQUEST_SLEEP,
) -> dict[str, int]:
    cache = load_fundamental_cache()
    cik_map = load_sec_ticker_map()
    updated = skipped = failed = 0
    for idx, code in enumerate(dict.fromkeys(normalize_code(c) for c in codes)):
        current = cache.get(code)
        if (
            not force
            and current
            and not _is_stale(current.get('updated_at'), FUNDAMENTAL_TTL_DAYS)
        ):
            skipped += 1
            continue
        try:
            cache[code] = fetch_sec_fundamentals(code, cik_map=cik_map)
            if cache[code].get('status') == 'ok':
                updated += 1
            else:
                failed += 1
        except Exception as exc:
            cache[code] = {
                'code': code,
                'ticker': normalize_ticker(code),
                'status': 'error',
                'updated_at': _today_str(),
                'reason': str(exc),
            }
            failed += 1
        if sleep_seconds > 0 and idx < len(codes) - 1:
            time.sleep(sleep_seconds)
    save_fundamental_cache(cache)
    return {
        'updated': updated,
        'skipped': skipped,
        'failed': failed,
        'total': len(dict.fromkeys(normalize_code(c) for c in codes)),
    }
