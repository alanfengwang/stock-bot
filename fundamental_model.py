"""
fundamental_model.py — 行业分模板的慢速基本面评分
"""

from __future__ import annotations

from typing import Any


GROWTH_SECTORS = {'AI软件', 'AI芯片', '光模块'}
QUALITY_SECTORS = {'大型科技', '医疗/生物', '消费/零售'}
FINANCIAL_SECTORS = {'金融科技'}
CYCLICAL_SECTORS = {'存储', '太空国防', '电力能源', '金属矿物'}
ETF_SECTORS = {'指数ETF'}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _score_linear(value: float | None, bad: float, good: float) -> float:
    if value is None:
        return 50.0
    if good == bad:
        return 50.0
    raw = (value - bad) / (good - bad) * 100
    return _clamp(raw)


def _score_inverse(value: float | None, good: float, bad: float) -> float:
    if value is None:
        return 50.0
    if bad == good:
        return 50.0
    raw = (bad - value) / (bad - good) * 100
    return _clamp(raw)


def _avg(*values: float) -> float:
    items = [v for v in values if v is not None]
    if not items:
        return 50.0
    return sum(items) / len(items)


def _safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return float(num) / float(den)


def _component_score(metrics: dict, snapshot: dict, sector: str) -> dict[str, float]:
    revenue_yoy = metrics.get('revenue_yoy')
    revenue_accel = metrics.get('revenue_accel')
    gross_margin = metrics.get('gross_margin')
    operating_margin = metrics.get('operating_margin')
    operating_margin_delta = metrics.get('operating_margin_delta')
    fcf_margin = metrics.get('fcf_margin')
    net_debt = metrics.get('net_debt')
    revenue_annual = metrics.get('revenue_annual')
    total_debt = metrics.get('total_debt')
    roe = metrics.get('roe')
    roic = metrics.get('roic')

    pe = snapshot.get('pe_ttm_ratio')
    pb = snapshot.get('pb_ratio')
    market_cap = snapshot.get('total_market_val')
    price_to_sales = _safe_div(market_cap, revenue_annual)
    debt_to_revenue = _safe_div(total_debt, revenue_annual)
    net_debt_to_revenue = _safe_div(net_debt, revenue_annual)
    rule40 = None
    if revenue_yoy is not None and fcf_margin is not None:
        rule40 = revenue_yoy + fcf_margin

    growth = _avg(
        _score_linear(revenue_yoy, -0.05, 0.25),
        _score_linear(revenue_accel, -0.08, 0.08),
    )
    margins = _avg(
        _score_linear(gross_margin, 0.20, 0.70),
        _score_linear(operating_margin, -0.05, 0.25),
        _score_linear(rule40, 0.05, 0.40),
    )
    cashflow = _avg(
        _score_linear(fcf_margin, -0.05, 0.20),
    )
    balance = _avg(
        _score_inverse(net_debt_to_revenue, 0.0, 1.5),
        _score_inverse(debt_to_revenue, 0.2, 1.8),
    )
    efficiency = _avg(
        _score_linear(roe, 0.05, 0.25),
        _score_linear(roic, 0.06, 0.20),
    )
    operating_momentum = _avg(
        _score_linear(revenue_accel, -0.08, 0.08),
        _score_linear(operating_margin_delta, -0.03, 0.05),
    )

    if sector in GROWTH_SECTORS:
        valuation = _avg(
            _score_inverse(price_to_sales, 4.0, 20.0),
            _score_inverse(pe, 25.0, 120.0),
        )
    elif sector in QUALITY_SECTORS:
        valuation = _avg(
            _score_inverse(pe, 15.0, 40.0),
            _score_inverse(pb, 3.0, 12.0),
        )
    elif sector in FINANCIAL_SECTORS:
        valuation = _avg(
            _score_inverse(pb, 1.5, 6.0),
            _score_inverse(pe, 10.0, 35.0),
        )
        margins = _avg(
            _score_linear(operating_margin, 0.05, 0.35),
            _score_linear(fcf_margin, 0.0, 0.25),
        )
    elif sector in CYCLICAL_SECTORS:
        valuation = _avg(
            _score_inverse(pe, 8.0, 25.0),
            _score_inverse(price_to_sales, 1.0, 5.0),
        )
        growth = _avg(
            _score_linear(revenue_yoy, -0.10, 0.18),
            _score_linear(revenue_accel, -0.10, 0.10),
        )
    else:
        valuation = _avg(
            _score_inverse(pe, 15.0, 45.0),
            _score_inverse(price_to_sales, 2.0, 10.0),
        )

    return {
        'growth': growth,
        'margins': margins,
        'cashflow': cashflow,
        'balance': balance,
        'efficiency': efficiency,
        'valuation': valuation,
        'operating_momentum': operating_momentum,
    }


def _weights_for_sector(sector: str) -> dict[str, float]:
    if sector in GROWTH_SECTORS:
        return {
            'growth': 0.25,
            'margins': 0.20,
            'cashflow': 0.15,
            'balance': 0.10,
            'efficiency': 0.10,
            'valuation': 0.10,
            'operating_momentum': 0.10,
        }
    if sector in QUALITY_SECTORS:
        return {
            'growth': 0.15,
            'margins': 0.20,
            'cashflow': 0.20,
            'balance': 0.15,
            'efficiency': 0.20,
            'valuation': 0.10,
        }
    if sector in FINANCIAL_SECTORS:
        return {
            'growth': 0.20,
            'margins': 0.20,
            'balance': 0.20,
            'efficiency': 0.20,
            'valuation': 0.20,
        }
    if sector in CYCLICAL_SECTORS:
        return {
            'growth': 0.10,
            'margins': 0.15,
            'cashflow': 0.25,
            'balance': 0.25,
            'efficiency': 0.10,
            'valuation': 0.15,
        }
    return {
        'growth': 0.18,
        'margins': 0.18,
        'cashflow': 0.18,
        'balance': 0.16,
        'efficiency': 0.15,
        'valuation': 0.15,
    }


def _notes_from_components(components: dict[str, float]) -> list[str]:
    ordered = sorted(components.items(), key=lambda item: item[1], reverse=True)
    notes: list[str] = []
    if ordered:
        notes.append(f"强项:{ordered[0][0]} {ordered[0][1]:.0f}")
    weak = sorted(components.items(), key=lambda item: item[1])
    if weak:
        notes.append(f"短板:{weak[0][0]} {weak[0][1]:.0f}")
    return notes


def _coerce_snapshot(snapshot: Any) -> dict[str, float]:
    if snapshot is None:
        return {}
    if isinstance(snapshot, dict):
        source = snapshot
    else:
        source = {k: snapshot.get(k) for k in ['pe_ttm_ratio', 'pb_ratio', 'total_market_val']}
    out: dict[str, float] = {}
    for key in ['pe_ttm_ratio', 'pb_ratio', 'total_market_val']:
        try:
            val = source.get(key)
            out[key] = float(val) if val not in (None, '') else None
        except (TypeError, ValueError):
            out[key] = None
    return out


def score_slow_fundamentals(
    code: str,
    sector: str,
    entry: dict | None,
    snapshot: Any = None,
) -> dict:
    if sector in ETF_SECTORS:
        return {
            'available': True,
            'score': 70.0,
            'components': {'etf': 70.0},
            'notes': ['ETF: 默认走被动配置，不做公司基本面深评'],
            'source': entry.get('source') if entry else None,
            'updated_at': entry.get('updated_at') if entry else None,
        }

    if not entry or entry.get('status') != 'ok':
        return {
            'available': False,
            'score': None,
            'components': {},
            'notes': ['慢基本面缺失'],
            'source': entry.get('source') if entry else None,
        }

    metrics = entry.get('metrics', {})
    snapshot_dict = _coerce_snapshot(snapshot)
    components = _component_score(metrics, snapshot_dict, sector)
    weights = _weights_for_sector(sector)

    score = 0.0
    total_weight = 0.0
    for key, weight in weights.items():
        if key in components:
            score += components[key] * weight
            total_weight += weight
    score = score / total_weight if total_weight else 50.0

    return {
        'available': True,
        'score': round(_clamp(score), 1),
        'components': {k: round(v, 1) for k, v in components.items()},
        'notes': _notes_from_components(components),
        'source': entry.get('source'),
        'updated_at': entry.get('updated_at'),
    }
