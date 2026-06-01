"""
execution_policy.py — 统一的买入事件与仓位预算规则

目标：
1. 混合模式下，底仓脚本和自动 bot 共用一套金额规范
2. 不同入场事件有不同的预算倍率和风险倍率
3. 所有买入都先保留现金储备，再决定可用预算
"""

from __future__ import annotations

CASH_RESERVE_RATIO = 0.20

ENTRY_SIZE_POLICY: dict[str, dict[str, float | str]] = {
    # 广泛底仓：固定美元金额
    'micro_position': {
        'kind': 'fixed_cash',
        'cash': 500.0,
        'risk_mult': 1.00,
    },
    # 新仓：完整信号仓位
    'golden_cross': {
        'kind': 'bucket_alloc',
        'alloc_mult': 1.00,
        'risk_mult': 1.00,
    },
    # 趋势中回踩确认：半仓到六成仓
    'trend_pullback': {
        'kind': 'bucket_alloc',
        'alloc_mult': 0.60,
        'risk_mult': 0.75,
    },
    # 突破追随：仓位略低于金叉，避免过度追高
    'breakout': {
        'kind': 'bucket_alloc',
        'alloc_mult': 0.80,
        'risk_mult': 0.90,
    },
    # starter 仓位晋级：按目标仓位缺口补仓
    'starter_promotion': {
        'kind': 'position_gap',
        'alloc_mult': 0.60,
        'position_mult': 1.00,
        'risk_mult': 0.80,
    },
    # 常规加码：最多补当前持仓的一半
    'add_position': {
        'kind': 'position_gap',
        'alloc_mult': 0.50,
        'position_mult': 0.50,
        'risk_mult': 0.80,
    },
    # 蓝筹底仓：趋势确认直接建仓（高质量股不等信号）
    'uptrend': {
        'kind': 'bucket_alloc',
        'alloc_mult': 1.00,
        'risk_mult': 1.00,
    },
    # 热门赛道小仓位（低评分股，控制风险）
    'hot_sector': {
        'kind': 'bucket_alloc',
        'alloc_mult': 0.40,
        'risk_mult': 0.60,
    },
    # 分批建仓 第二批（+5% 利润触发，占目标仓位约 30%）
    'pyramid_stage2': {
        'kind': 'bucket_alloc',
        'alloc_mult': 0.30,
        'risk_mult': 0.80,
    },
    # 分批建仓 第三批（+12% 利润触发，占目标仓位约 30%）
    'pyramid_stage3': {
        'kind': 'bucket_alloc',
        'alloc_mult': 0.30,
        'risk_mult': 0.70,
    },
}

ENTRY_REASON_LABEL: dict[str, str] = {
    'uptrend':      '趋势底仓',
    'hot_sector':   '热门赛道',
    'golden_cross': '金叉',
    'trend_pullback': '回踩确认',
    'breakout': '突破',
    'starter_promotion': 'starter晋级',
    'add_position': '加码',
    'micro_position': '底仓',
    'pyramid_stage2': '金字塔第二批',
    'pyramid_stage3': '金字塔第三批',
}


def reserve_cash(initial_cash: float,
                 reserve_ratio: float = CASH_RESERVE_RATIO) -> float:
    return max(0.0, initial_cash * reserve_ratio)


def available_cash(cash: float,
                   initial_cash: float,
                   reserve_ratio: float = CASH_RESERVE_RATIO) -> float:
    return max(0.0, cash - reserve_cash(initial_cash, reserve_ratio))


def target_bucket_cash(initial_cash: float, bucket_alloc: float) -> float:
    return max(0.0, initial_cash * bucket_alloc)


def is_starter_position(current_value: float,
                        initial_cash: float,
                        bucket_alloc: float,
                        threshold: float = 0.35) -> bool:
    target_cash = target_bucket_cash(initial_cash, bucket_alloc)
    if target_cash <= 0:
        return False
    return current_value <= target_cash * threshold


def entry_budget(cash: float,
                 initial_cash: float,
                 bucket_alloc: float,
                 reason: str,
                 reserve_ratio: float = CASH_RESERVE_RATIO,
                 current_position_value: float = 0.0) -> float:
    """
    给定事件类型，返回本次允许使用的标准化预算。
    - fixed_cash    : 固定金额（底仓）
    - bucket_alloc  : 目标桶仓位 × 倍率
    - position_gap  : 目标仓位缺口内补仓，并限制补仓幅度
    """
    policy = ENTRY_SIZE_POLICY.get(reason)
    if policy is None:
        raise KeyError(f'未知入场原因: {reason}')

    liquid_cash = available_cash(cash, initial_cash, reserve_ratio)
    if liquid_cash <= 0:
        return 0.0

    kind = str(policy['kind'])
    if kind == 'fixed_cash':
        return min(liquid_cash, float(policy['cash']))

    bucket_cash = target_bucket_cash(initial_cash, bucket_alloc)
    if bucket_cash <= 0:
        return 0.0

    budget = min(liquid_cash, bucket_cash * float(policy.get('alloc_mult', 1.0)))

    if kind == 'position_gap':
        gap = max(0.0, bucket_cash - current_position_value)
        budget = min(budget, gap)
        if current_position_value > 0:
            pos_mult = float(policy.get('position_mult', 1.0))
            budget = min(budget, current_position_value * pos_mult)

    return max(0.0, budget)


def atr_position_qty(price: float,
                     atr: float,
                     budget: float,
                     risk_per_trade: float = 500.0,
                     atr_mult: float = 2.0,
                     reason: str = 'golden_cross') -> int:
    if price <= 0 or budget < price:
        return 0

    policy = ENTRY_SIZE_POLICY.get(reason, {})
    stop_dist = max(atr * atr_mult, price * 0.01)
    risk_cash = risk_per_trade * float(policy.get('risk_mult', 1.0))

    qty_risk = max(1, int(risk_cash / stop_dist))
    qty_budget = max(1, int(budget / price))
    return min(qty_risk, qty_budget)


def cash_position_qty(price: float, budget: float) -> int:
    if price <= 0 or budget < price:
        return 0
    return max(1, int(budget / price))
