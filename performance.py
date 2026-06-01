"""
performance.py — 统一绩效指标计算
"""

from __future__ import annotations

import math


def calc_pnl_metrics(pnls: list[float],
                     initial_cash: float,
                     n_periods: int,
                     annualization: int = 252) -> dict:
    if not pnls:
        return {
            'total_trades': 0,
            'win_rate': 0.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'profit_factor': 0.0,
            'total_pnl': 0.0,
            'total_ret': 0.0,
            'ann_ret': 0.0,
            'max_dd': 0.0,
            'sharpe': 0.0,
        }

    total_pnl = sum(pnls)
    total_ret = total_pnl / initial_cash if initial_cash else 0.0
    ann_ret = (1 + total_ret) ** (annualization / max(n_periods, 1)) - 1 if total_ret > -1 else -1.0

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / len(pnls)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    profit_factor = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else float('inf')

    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        cum += pnl
        peak = max(peak, cum)
        drawdown = (peak - cum) / initial_cash if initial_cash else 0.0
        max_dd = max(max_dd, drawdown)

    if len(pnls) > 1:
        returns = [p / initial_cash for p in pnls]
        mean = sum(returns) / len(returns)
        variance = sum((x - mean) ** 2 for x in returns) / len(returns)
        sharpe = mean / (math.sqrt(variance) + 1e-10) * math.sqrt(annualization)
    else:
        sharpe = 0.0

    return {
        'total_trades': len(pnls),
        'win_rate': win_rate,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': profit_factor,
        'total_pnl': total_pnl,
        'total_ret': total_ret,
        'ann_ret': ann_ret,
        'max_dd': max_dd,
        'sharpe': sharpe,
    }


def closed_trade_pnls(trades: list[dict]) -> list[float]:
    return [
        float(t['pnl'])
        for t in trades
        if t.get('side') in ('SELL', 'SELL_HALF', 'SELL_PARTIAL') and t.get('pnl') is not None
    ]
