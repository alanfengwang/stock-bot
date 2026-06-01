"""
trade_costs.py — 统一手续费与滑点模型
"""

from __future__ import annotations

COMMISSION_RATE = 0.0003
MIN_COMMISSION = 1.0


def calc_commission(price: float,
                    qty: int,
                    rate: float = COMMISSION_RATE,
                    minimum: float = MIN_COMMISSION) -> float:
    return max(minimum, price * qty * rate)


def apply_slippage(price: float,
                   side: str,
                   bps: float = 0.0) -> float:
    if bps <= 0:
        return price
    adj = bps / 10_000.0
    if side.upper() == 'BUY':
        return price * (1 + adj)
    if side.upper() == 'SELL':
        return price * (1 - adj)
    return price
