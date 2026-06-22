"""
market_regime_example.py — 接入示例

展示如何把 market_regime.py 的四个模块
接入现有的 portfolio_bot / bucket_runner 信号流程。

在你现有的买入逻辑中，原来是：
    result = detect_entry_signal(...)
    if result:
        place_order(...)

现在改为：
    result = detect_entry_signal(...)
    if result:
        ef = entry_filter(df, event_type=result[0], ...)
        if ef.allowed:
            place_order(...)
        else:
            log(ef.summary)   # 记录拦截原因
"""

from __future__ import annotations

import pandas as pd

from market_regime import (
    EmotionPhase,
    Regime,
    classify_regime,
    emotion_phase,
    entry_filter,
    score_signal,
    sector_gate,
)
from strategy_config import SECTOR_MAP
from strategy_signals import (
    calc_macd,
    enrich_indicators,
    detect_entry_signal,
)


# ──────────────────────────────────────────────────────────────────
# 示例 A：最轻量接入（只加信号评分，不依赖 SPY 数据）
# ──────────────────────────────────────────────────────────────────

def check_entry_with_score(
    df: pd.DataFrame,
    cfg: dict,
    bucket: str,
    score_threshold: float = 12.0,
) -> tuple[str, str, float] | None:
    """
    在现有 detect_entry_signal 之后追加评分过滤。

    返回 (event_type, reason, score) 或 None
    """
    df = enrich_indicators(df, cfg)
    latest   = df.iloc[-1]
    prev_row = df.iloc[-2]

    fast_now  = float(latest.get('fast_ma', 0))
    slow_now  = float(latest.get('slow_ma_v', 0))
    fast_prev = float(prev_row.get('fast_ma', 0))
    slow_prev = float(prev_row.get('slow_ma_v', 0))

    result = detect_entry_signal(
        cfg=cfg, df=df, latest=latest, prev_row=prev_row,
        fast_now=fast_now, slow_now=slow_now,
        fast_prev=fast_prev, slow_prev=slow_prev,
        signal_ok=True,
    )
    if result is None:
        return None

    event_type, reason = result
    ss = score_signal(df, event_type, bucket=bucket)

    if not ss.tradeable:
        print(f'[评分过滤] {event_type} 评分{ss.total:.0f}<{score_threshold} | {ss.reasons}')
        return None

    return event_type, reason, ss.total


# ──────────────────────────────────────────────────────────────────
# 示例 B：完整过滤（市场状态 + 评分 + 板块 + 情绪）
# ──────────────────────────────────────────────────────────────────

def full_entry_check(
    ticker: str,
    df: pd.DataFrame,
    cfg: dict,
    bucket: str,
    spy_df: pd.DataFrame,
    qqq_df: pd.DataFrame | None = None,
    vix: float | None = None,
    sector_etf_map: dict[str, pd.DataFrame] | None = None,
) -> tuple[str, str] | None:
    """
    完整入场检查流程：
      detect_entry_signal → entry_filter（四模块）→ 返回或拦截

    sector_etf_map 示例：
      {
        'AI芯片':   soxl_df,    # 或用 SOXX ETF 的日线
        '大型科技':  qqq_df,
        '太空国防':  xar_df,
        '电力能源':  xle_df,
        ...
      }
    """
    df = enrich_indicators(df, cfg)
    latest   = df.iloc[-1]
    prev_row = df.iloc[-2]

    fast_now  = float(latest.get('fast_ma', 0))
    slow_now  = float(latest.get('slow_ma_v', 0))
    fast_prev = float(prev_row.get('fast_ma', 0))
    slow_prev = float(prev_row.get('slow_ma_v', 0))

    result = detect_entry_signal(
        cfg=cfg, df=df, latest=latest, prev_row=prev_row,
        fast_now=fast_now, slow_now=slow_now,
        fast_prev=fast_prev, slow_prev=slow_prev,
        signal_ok=True,
    )
    if result is None:
        return None

    event_type, signal_reason = result

    # 个股所属板块（从 SECTOR_MAP 查）
    sector = SECTOR_MAP.get(ticker)

    ef = entry_filter(
        df=df,
        event_type=event_type,
        bucket=bucket,
        spy_df=spy_df,
        qqq_df=qqq_df,
        vix=vix,
        sector_df_map=sector_etf_map,
        sector=sector,
    )

    if ef.allowed:
        full_reason = f'{signal_reason} | 评分{ef.signal_score.total:.0f} | {ef.summary}'
        return event_type, full_reason
    else:
        print(f'[入场拦截] {ticker} {ef.summary}')
        return None


# ──────────────────────────────────────────────────────────────────
# 示例 C：情绪判断单独使用（辅助加减仓决策）
# ──────────────────────────────────────────────────────────────────

def emotion_position_hint(df: pd.DataFrame) -> str:
    """
    根据情绪阶段给出仓位建议文字，可用于日志或推送。

    示例输出：
      '情绪底部(4/5项)：逆向布局区 → 可加仓至满仓'
      '情绪顶部(4/5项)：减仓警戒区 → 止盈一半'
    """
    em = emotion_phase(df)
    hints = {
        EmotionPhase.BOTTOM:  '逆向布局区 → 可加仓至满仓',
        EmotionPhase.WARMING: '情绪启动 → 正常建仓',
        EmotionPhase.NORMAL:  '正常区间 → 维持仓位',
        EmotionPhase.HEATING: '追高风险 → 减少新开仓',
        EmotionPhase.TOP:     '狂热顶部 → 止盈减仓',
    }
    return f'{em.reason} → {hints[em.phase]}'


# ──────────────────────────────────────────────────────────────────
# 示例 D：市场状态作为盘前日志
# ──────────────────────────────────────────────────────────────────

def morning_regime_report(
    spy_df: pd.DataFrame,
    qqq_df: pd.DataFrame,
    vix: float,
) -> str:
    """
    盘前打印市场状态摘要，可替代手动判断大盘。

    示例输出：
      '📊 市场状态: trending_up | 得分+42 | MA多头✓ | VIX=16.2 正常
       建议：正常运行所有桶策略'
    """
    r = classify_regime(spy_df, qqq_df, vix)

    advice = {
        Regime.TRENDING_UP:   '正常运行所有桶策略',
        Regime.SECTOR_HOT:    '保守桶降频，重点跑 shortterm / longterm 热点桶',
        Regime.SIDEWAYS:      '缩减新开仓，等待方向确认',
        Regime.VOLATILE:      '暂停短线桶，保守桶降仓至 50%',
        Regime.TRENDING_DOWN: '全线停止买入，执行止损计划',
    }

    return (
        f'📊 市场状态: {r.regime.value} | '
        f'得分{r.score:+.0f} | '
        f'MA多头{"✓" if r.ma_aligned else "✗"} | '
        f'VIX={vix:.1f} {r.vix_level}\n'
        f'   原因: {r.reason}\n'
        f'   建议: {advice[r.regime]}'
    )
