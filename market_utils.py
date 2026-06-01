"""
market_utils.py — 快照价格与行情读取小工具
"""

from __future__ import annotations

from typing import cast

import pandas as pd
from moomoo import AuType, RET_OK


def live_price_from_row(row: pd.Series) -> float:
    """
    当前最可交易价格，优先取最新活跃价：

    优先级：
      1. bid/ask 中间价（两者均有时最精确）
      2. 盘前价  pre_price      ← 盘前时段最实时
      3. 盘后价  after_price    ← 盘后时段最实时
      4. 夜盘价  overnight_price（已收盘，较 pre/after 过时）
      5. 最新价  last_price     ← 正常交易时段 / 最后收盘

    注：overnight_price 是夜盘收盘价，优先级低于 pre/after，
        避免盘前时段错误沿用过时的夜盘价格。
    """
    bid       = float(row.get('bid_price')       or 0)
    ask       = float(row.get('ask_price')        or 0)
    pre       = float(row.get('pre_price')        or 0)
    after     = float(row.get('after_price')      or 0)
    overnight = float(row.get('overnight_price')  or 0)
    last      = float(row.get('last_price')       or 0)

    # bid/ask 双向均有报价时，取中间价（最精确）
    if bid > 0 and ask > 0:
        return round((bid + ask) / 2, 4)

    return pre or after or overnight or last


def request_kline(ctx, stock: str, ktype, n: int) -> pd.DataFrame | None:
    ret, df, _ = ctx.request_history_kline(
        stock, ktype=ktype, autype=AuType.QFQ, max_count=n
    )
    df = cast(pd.DataFrame, df)
    return df if ret == RET_OK and len(df) >= n - 2 else None
