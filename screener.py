"""
screener.py — 基于 moomoo API 的基本面 + 量能评分（无需外部 key）

评分逻辑：
  fundamental_score  0-10  来自 snapshot：PE、EPS、市值、PB
  volume_signal      positive / negative / neutral  来自 K 线量价关系

买入前两项都通过才下单：
  - fundamental_score >= MIN_FUND_SCORE（默认 4）
  - volume_signal != "negative"

PE 评分说明（bucket 感知版）：
  保守桶（conservative）：蓝筹风格，低 PE 优先，> 60 视为高估
  成长桶（longterm）：AI/芯片，高成长允许高 PE，> 150 才扣分
  短线桶（shortterm）：动量/主题，PE 不是核心因子，只要盈利即通过
"""

from __future__ import annotations
import pandas as pd

# 各桶最低基本面门槛（保守要求最高）
MIN_FUND_SCORE: dict[str, float] = {
    'conservative': 3.0,   # 放宽：底仓观察模式
    'longterm':     2.5,
    'shortterm':    1.5,   # 短线只要盈利即可通过
}

# 各桶的 PE 容忍上限（超过此值才开始扣分）
_PE_GROWTH_CEILING: dict[str, float] = {
    'conservative': 30.0,   # 蓝筹：PE > 30 开始扣分
    'longterm':     80.0,   # 成长：PE > 80 才扣分（NVDA/MRVL 常态）
    'shortterm':    120.0,  # 短线：PE > 120 才扣分
    'default':      60.0,
}


def fundamental_score(
    row: pd.Series,
    bucket: str = 'default',
) -> tuple[float, list[str]]:
    """
    从 get_market_snapshot 的一行数据计算基本面评分。
    返回 (score 0-10, notes 列表)

    修复：原版对 PE > 60 统一扣分，误杀 NVDA/PLTR/APP 等高成长股。
    现改为 bucket 感知版：longterm / shortterm 桶使用更宽松的 PE 阈值。
    """
    score = 5.0
    notes: list[str] = []

    pe  = float(row.get('pe_ttm_ratio')   or 0)
    eps = float(row.get('earning_per_share') or 0)
    pb  = float(row.get('pb_ratio')       or 0)
    cap = float(row.get('total_market_val') or 0)   # 总市值（美元）

    # ── 盈利性：亏损直接降到底 ───────────────────
    if eps <= 0:
        # shortterm 桶允许部分尚未盈利的高成长公司（如 ASTS、LUNR）
        # 但仍给低分，靠技术信号强度补偿
        if bucket == 'shortterm':
            return 2.0, ['⚠️ 未盈利(shortterm允许)']
        return 1.0, ['❌ 亏损/无EPS']
    notes.append('✅ 盈利')

    # ── 市值规模 ──────────────────────────────────
    if cap > 5e11:           # > $5000亿，超大盘
        score += 2.0; notes.append('超大盘')
    elif cap > 1e11:         # > $1000亿
        score += 1.0; notes.append('大盘')
    elif cap > 1e10:         # > $100亿
        score += 0.0; notes.append('中盘')
    elif cap > 0:
        score -= 0.5; notes.append('小盘(谨慎)')

    # ── PE 估值（bucket 感知）────────────────────
    pe_ceiling = _PE_GROWTH_CEILING.get(bucket, _PE_GROWTH_CEILING['default'])

    if pe <= 0:
        pass  # 数据缺失，不加减分
    elif pe <= 15:
        score += 2.0; notes.append(f'PE={pe:.0f} 低估')
    elif pe <= 30:
        score += 1.5; notes.append(f'PE={pe:.0f} 合理')
    elif pe <= pe_ceiling:
        # 在桶的容忍范围内：成长溢价，不扣分
        score += 0.5; notes.append(f'PE={pe:.0f} 成长溢价')
    elif pe <= pe_ceiling * 1.5:
        score -= 0.3; notes.append(f'PE={pe:.0f} 偏高')
    else:
        score -= 1.0; notes.append(f'PE={pe:.0f} 过高')

    # ── PB 账面价值 ───────────────────────────────
    # 科技成长股 PB 普遍高，只在极端情况扣分
    if 0 < pb <= 5:
        score += 0.5; notes.append(f'PB={pb:.1f}')
    elif pb > 50 and bucket == 'conservative':
        score -= 0.5; notes.append(f'PB={pb:.1f} 偏高')

    return round(min(10.0, max(0.0, score)), 1), notes


def volume_signal(df: pd.DataFrame,
                  recent_bars: int = 5,
                  baseline_bars: int = 20) -> tuple[str, str]:
    """
    量价配合作为新闻事件代理信号。

    原理：
      - 最近 N 根 K 线均量 vs 前 M 根均量（baseline）
      - 量放大 + 价涨  ≈ 潜在利好事件（机构买入/好消息）
      - 量放大 + 价跌  ≈ 潜在利空事件（出货/坏消息）
      - 量缩或正常    ≈ 中性

    返回 ('positive'|'negative'|'neutral', 说明文字)
    """
    needed = recent_bars + baseline_bars + 1
    if len(df) < needed:
        return 'neutral', '数据不足'

    recent   = df.iloc[-recent_bars:]
    baseline = df.iloc[-(recent_bars + baseline_bars): -recent_bars]

    avg_recent   = recent['volume'].mean()
    avg_baseline = baseline['volume'].mean()
    if avg_baseline == 0:
        return 'neutral', '无量参考'

    vol_ratio = avg_recent / avg_baseline

    price_start = float(df.iloc[-(recent_bars + 1)]['close'])
    price_end   = float(df.iloc[-1]['close'])
    price_chg   = (price_end - price_start) / price_start if price_start else 0

    if vol_ratio >= 2.0:
        if price_chg >= 0.03:
            return 'positive', f'量增{vol_ratio:.1f}x 价涨{price_chg*100:.1f}%（疑似利好）'
        elif price_chg <= -0.03:
            return 'negative', f'量增{vol_ratio:.1f}x 价跌{price_chg*100:.1f}%（疑似利空）'
        else:
            return 'neutral', f'量增{vol_ratio:.1f}x 方向不明'
    elif vol_ratio >= 1.5:
        return 'neutral', f'量能略增{vol_ratio:.1f}x'
    else:
        return 'neutral', f'量能正常{vol_ratio:.1f}x'
