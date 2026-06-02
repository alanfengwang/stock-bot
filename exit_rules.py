"""
exit_rules.py — 纯逻辑持仓退出规则
"""

from __future__ import annotations

from datetime import datetime


def holding_days(entry_time: str | None, now: datetime | None = None) -> int:
    if not entry_time:
        return 0
    try:
        opened_at = datetime.strptime(entry_time, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return 0
    current = now or datetime.now()
    return max(0, (current - opened_at).days)


def evaluate_trailing_stop(
    entry_price: float,
    current_price: float,
    trail_high: float,
    atr_value: float,
    activate_profit: float = 0.03,
    break_even_profit: float = 0.05,
    break_even_buffer: float = 0.001,
    atr_mult: float = 1.5,
) -> dict:
    if entry_price <= 0:
        return {
            'high_profit': 0.0,
            'trail_active': False,
            'break_even_active': False,
            'base_stop': current_price,
            'effective_stop': current_price,
            'triggered': False,
        }

    high_profit = (trail_high - entry_price) / entry_price
    base_stop = trail_high - atr_value * atr_mult
    trail_active = high_profit >= activate_profit
    break_even_active = high_profit >= break_even_profit
    effective_stop = base_stop

    if break_even_active:
        effective_stop = max(effective_stop, entry_price * (1.0 + break_even_buffer))

    return {
        'high_profit': high_profit,
        'trail_active': trail_active,
        'break_even_active': break_even_active,
        'base_stop': base_stop,
        'effective_stop': effective_stop,
        'triggered': trail_active and current_price < effective_stop,
    }


def should_time_stop(
    entry_time: str | None,
    pnl_pct: float,
    max_days: int = 0,
    min_return: float = 0.0,
    now: datetime | None = None,
) -> bool:
    if max_days <= 0:
        return False
    return holding_days(entry_time, now=now) >= max_days and pnl_pct < min_return
