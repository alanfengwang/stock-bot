"""
entry_engine.py — 入场信号级联与分层买入

职责：
  - build_signal_cascade(): 按优先级聚合所有 10 种入场信号
  - tiered_entry(): 根据基本面评分分三层决定仓位
  - 调用 order_executor.execute_buy() 下单
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import time as _time

from discussion_universe import discussion_alloc_modifier, load_discussion_universe
from execution_policy import (
    ENTRY_REASON_LABEL, atr_position_qty, entry_budget
)
from order_executor import execute_buy
from screener import MIN_FUND_SCORE
from shared_state import (
    broker, count_bucket_positions, get_regime, has_open_order,
    is_circuit_breaker_active, get_circuit_breaker_status,
)
from strategy_config import (
    CASH_RESERVE,
    ENTRY_HARD_REJECT_FUND_FLOOR_RATIO,
    ENTRY_PROBE_ALLOC_MULT,
    ENTRY_SCORE_FULL,
    ENTRY_SCORE_PROBE,
    ENTRY_SCORE_SCALE,
    ENTRY_SIGNAL_THRESHOLD,
    ENTRY_SIGNAL_THRESHOLD_HIGH_QUALITY,
    ENTRY_SIGNAL_THRESHOLD_MID_QUALITY,
    SECTOR_MAP,
)
from market_regime import score_signal, EmotionPhase, emotion_phase
from strategy_signals import (
    detect_entry_signal,
    detect_uptrend,
    detect_premarket_signal,
    detect_momentum_surge,
    detect_rsi_bounce,
    detect_bollinger_breakout,
    detect_52w_high_breakout,
    detect_macd_zero_cross,
)


# 入场前行业集中度上限（与风控层的 max_industry_pct 保持一致）
_PRE_ENTRY_SECTOR_CAP = 0.30

# 市场状态 → alloc_mult 缩放
_REGIME_SCALE = {'BULL': 1.00, 'NEUTRAL': 0.85, 'BEAR': 0.70}

# 同板块持仓数 → 相关性惩罚系数
_CORR_PENALTY = {0: 1.00, 1: 0.90, 2: 0.80}
_CORR_PENALTY_MAX = 0.70   # 3只及以上同板块持仓时的下限

# 讨论热度缓存：TTL 3600s，与 refresh_discussion_universe 的刷新周期对齐
_discussion_feed_cache: dict = {}
_discussion_feed_ts: float   = 0.0
_DISCUSSION_CACHE_TTL        = 3600.0   # 1小时


@dataclass
class EntryAdmission:
    total: float
    action: str
    alloc_mult: float
    notes: list[str]


def signal_threshold_for_quality(
    base_threshold: float,
    quality_score: float | None,
) -> float:
    """高质量股票放宽最低信号分，允许更自然的趋势建仓。"""
    if quality_score is None:
        return base_threshold
    if quality_score >= 7.5:
        return min(base_threshold, ENTRY_SIGNAL_THRESHOLD_HIGH_QUALITY)
    if quality_score >= 6.0:
        return min(base_threshold, ENTRY_SIGNAL_THRESHOLD_MID_QUALITY)
    return base_threshold


def score_entry_candidate(
    *,
    bucket_name: str,
    signal_score: float,
    quality_score: float,
    fund_sc: float,
    skip_fast_fund_gate: bool,
    vol_sig: str = 'neutral',
    emotion_top: bool = False,
) -> EntryAdmission:
    """
    用综合评分替代串行一票否决。

    只把真正危险的情况留给硬风控；其余因素转为加减分，允许小仓试探。
    """
    notes: list[str] = []
    total = 0.0

    signal_component = max(0.0, min(45.0, signal_score * 3.2))
    total += signal_component
    notes.append(f"信号{signal_score:.1f}")

    quality_component = max(0.0, min(35.0, quality_score * 3.0))
    total += quality_component
    notes.append(f"质量{quality_score:.1f}")

    if skip_fast_fund_gate:
        total += min(6.0, max(0.0, fund_sc) * 0.6)
    else:
        fund_floor = float(MIN_FUND_SCORE.get(bucket_name, 3.0))
        if fund_sc < fund_floor * ENTRY_HARD_REJECT_FUND_FLOOR_RATIO:
            return EntryAdmission(
                total=0.0,
                action='reject',
                alloc_mult=0.0,
                notes=[f"快基本面过弱({fund_sc:.1f}/{fund_floor:.1f})"],
            )
        if fund_sc < fund_floor:
            total -= 12.0
            notes.append(f"快基本面低于门槛({fund_sc:.1f})")
        else:
            total += min(10.0, fund_sc * 1.2)

    if vol_sig == 'positive':
        total += 5.0
        notes.append("量价正面")
    elif vol_sig == 'negative':
        penalty = 12.0 if bucket_name == 'shortterm' else 9.0
        total -= penalty
        notes.append(f"量价负面(-{penalty:.0f})")

    if emotion_top:
        total -= 12.0
        notes.append("情绪顶部")

    regime = get_regime()
    regime_adj = {'BULL': 4.0, 'NEUTRAL': 0.0, 'BEAR': -10.0}.get(regime, 0.0)
    total += regime_adj
    if regime_adj > 0:
        notes.append(f"市场{regime}(+{regime_adj:.0f})")
    elif regime_adj < 0:
        notes.append(f"市场{regime}({regime_adj:.0f})")

    total = max(0.0, min(100.0, total))
    if total >= ENTRY_SCORE_FULL:
        return EntryAdmission(total=total, action='full', alloc_mult=1.0, notes=notes)
    if total >= ENTRY_SCORE_SCALE:
        return EntryAdmission(total=total, action='scaled', alloc_mult=0.75, notes=notes)
    if total >= ENTRY_SCORE_PROBE:
        notes.append("试探仓")
        return EntryAdmission(
            total=total,
            action='probe',
            alloc_mult=ENTRY_PROBE_ALLOC_MULT,
            notes=notes,
        )
    return EntryAdmission(total=total, action='reject', alloc_mult=0.0, notes=notes)


def _get_discussion_feed() -> dict:
    """返回讨论热度数据，超过 TTL 自动从磁盘重新加载。"""
    global _discussion_feed_cache, _discussion_feed_ts
    if _time.monotonic() - _discussion_feed_ts > _DISCUSSION_CACHE_TTL:
        _discussion_feed_cache = load_discussion_universe()
        _discussion_feed_ts    = _time.monotonic()
    return _discussion_feed_cache


def check_sector_concentration(stock: str, label: str) -> bool:
    """
    入场前检查目标股票所属行业的当前持仓占比。

    Returns:
        True  → 板块占比在上限内，可以入场
        False → 超出上限，跳过本次入场

    逻辑：
      1. 从 SECTOR_MAP 查目标股票的板块
      2. 从 broker 获取所有持仓（含成本价×数量作为市值近似）
      3. 计算该板块持仓价值 / 全部持仓总价值
      4. 若比例 > _PRE_ENTRY_SECTOR_CAP，打印提示并返回 False
    """
    sector = SECTOR_MAP.get(stock)
    if sector is None:
        return True   # 未收录的板块不限制

    state = broker.get_state()
    positions = state.get('positions', {})
    if not positions:
        return True

    total_value = 0.0
    sector_value = 0.0
    for code, pos in positions.items():
        cost = float(pos.get('avg_cost', pos.get('entry_price', 0)) or 0)
        qty  = int(pos.get('qty', 0) or 0)
        val  = cost * qty
        total_value += val
        if SECTOR_MAP.get(code) == sector:
            sector_value += val

    if total_value <= 0:
        return True

    ratio = sector_value / total_value
    if ratio >= _PRE_ENTRY_SECTOR_CAP:
        print(
            f"[{label}] {stock} 板块集中度预检未通过：{sector} 占比 "
            f"{ratio*100:.1f}% ≥ {_PRE_ENTRY_SECTOR_CAP*100:.0f}%，跳过入场"
        )
        return False

    return True


def compute_entry_scale(stock: str) -> tuple[float, str]:
    """
    综合计算入场仓位缩放系数，返回 (scale, note_str)。

    三层独立调整相乘，任意一层收缩都会反映到最终预算：

    1. 讨论热度（discussion_alloc_modifier）
       - 热榜 1-10  → ×0.75（过热，降权）
       - 热榜 11-50 → ×1.15（有动量，加成）

    2. 市场状态（Regime）
       - BULL    → ×1.00
       - NEUTRAL → ×0.85
       - BEAR    → ×0.70

    3. 板块内相关性（同板块现有持仓数）
       - 0 只同板块持仓 → ×1.00
       - 1 只           → ×0.90
       - 2 只           → ×0.80
       - 3 只及以上     → ×0.70

    最终 scale 被 clip 到 [0.30, 1.30]，避免极端情况。
    """
    notes: list[str] = []

    # ── 层1：讨论热度（TTL缓存，每小时自动刷新）────────────────
    disc_mult, disc_note = discussion_alloc_modifier(stock, _get_discussion_feed())
    if disc_note:
        notes.append(disc_note)

    # ── 层2：市场状态 ─────────────────────────────────────────
    regime = get_regime()
    regime_mult = _REGIME_SCALE.get(regime, 1.00)
    if regime != 'BULL':
        notes.append(f'regime={regime}(×{regime_mult:.2f})')

    # ── 层3：板块内相关性（用成本价估算持仓价值，不需要实时行情）─────
    sector = SECTOR_MAP.get(stock)
    corr_mult = 1.00
    if sector:
        state = broker.get_state()
        same_sector_count = sum(
            1 for code, pos in state.get('positions', {}).items()
            if SECTOR_MAP.get(code) == sector
        )
        corr_mult = _CORR_PENALTY.get(same_sector_count, _CORR_PENALTY_MAX)
        if same_sector_count > 0:
            notes.append(f'{sector}已持{same_sector_count}只(×{corr_mult:.2f})')

    scale = disc_mult * regime_mult * corr_mult
    scale = max(0.30, min(1.30, scale))
    note  = ' | '.join(notes) if notes else ''
    return scale, note


def build_signal_cascade(
    cfg: dict,
    df: pd.DataFrame,
    latest: pd.Series,
    prev_row: pd.Series,
    fast_now: float,
    slow_now: float,
    fast_prev: float,
    slow_prev: float,
    extra_buy: bool,
    snap_row: pd.Series | None,
    price: float,
    allow_uptrend: bool = False,
    bucket_name: str = 'default',
    score_threshold: float = ENTRY_SIGNAL_THRESHOLD,
    quality_score: float | None = None,
) -> tuple[str, str, float] | None:
    """
    收集全部候选信号，用 score_signal() 评分后返回得分最高者。

    修复原版问题：原版把 golden_cross 排在第一位，遇到金叉就直接返回，
    导致更强的 52w_high / bb_breakout / momentum_surge 永远被截断。
    现改为「先收集所有触发的信号 → 逐一评分 → 取最高分」策略。
    低于 score_threshold 分的信号一律丢弃，避免弱信号入场。

    信号强度参考（DSA 策略思想，适配美股）：
      强：52w_high(13) / bb_breakout(11) / macd_zero_cross(12) / breakout(14)
      中：trend_pullback(12) / momentum_surge(10) / golden_cross(10)
      弱：rsi_bounce(9) / uptrend(8) / premarket_gap(7)
    """
    candidates: list[tuple[str, str]] = []

    # ── 收集所有触发的信号 ─────────────────────────────────────────

    # 基础技术信号：金叉 / 回踩 / 突破（来自桶配置的 entry_modes）
    base_sig = detect_entry_signal(
        cfg, df, latest, prev_row,
        fast_now, slow_now, fast_prev, slow_prev,
        extra_buy,
    )
    if base_sig:
        candidates.append(base_sig)

    if allow_uptrend:
        uptrend_sig = detect_uptrend(df, fast_now, slow_now)
        if uptrend_sig:
            candidates.append(uptrend_sig)

    # MACD 零轴上穿（趋势由负转正，强度高于普通金叉）
    macd_sig = detect_macd_zero_cross(df)
    if macd_sig:
        candidates.append(macd_sig)

    # 52 周新高（机构行为确认，美股最可靠的强势信号之一）
    if snap_row is not None:
        h52_sig = detect_52w_high_breakout(snap_row, price, within_pct=0.03)
        if h52_sig:
            candidates.append(h52_sig)

    # 布林带收窄突破（蓄势爆发，适合 shortterm / longterm）
    bb_sig = detect_bollinger_breakout(df, squeeze_threshold=0.04)
    if bb_sig:
        candidates.append(bb_sig)

    # 动量加速（量价齐升，不等回踩直接追）
    mom_sig = detect_momentum_surge(df, fast_now, slow_now,
                                    vol_surge_mult=2.0, price_accel_pct=0.5)
    if mom_sig:
        candidates.append(mom_sig)

    # RSI 超卖反弹（逆向低吸，适合 conservative）
    rsi_sig = detect_rsi_bounce(df, oversold=32.0, recover=38.0)
    if rsi_sig:
        candidates.append(rsi_sig)

    # 盘前异动（风险高，最低优先级）
    if snap_row is not None:
        pre_sig = detect_premarket_signal(snap_row, min_gap_pct=2.0, min_pre_vol=50_000)
        if pre_sig:
            candidates.append(pre_sig)

    if not candidates:
        return None

    # ── 评分排序，取最高分 ─────────────────────────────────────────
    best_sig: tuple[str, str] | None = None
    best_score = -999.0

    for sig in candidates:
        event_type, reason = sig
        ss = score_signal(df, event_type, bucket=bucket_name)
        if ss.total > best_score:
            best_score = ss.total
            best_sig = sig

    min_signal_score = signal_threshold_for_quality(score_threshold, quality_score)
    if best_score < min_signal_score:
        print(f"[信号过滤] 最优信号评分{best_score:.0f}<{min_signal_score:.0f}，放弃入场")
        return None

    assert best_sig is not None
    return best_sig[0], best_sig[1], best_score


def tiered_entry(
    ctx,
    stock: str,
    signal: tuple[str, str, float],
    fund_sc: float,
    fund_notes: list,
    vol_sig: str,
    vol_note: str,
    cfg: dict,
    bucket_name: str,
    label: str,
    ind_str: str,
    df: pd.DataFrame,
    price: float,
    atr_val: float,
    cash: float,
    initial_cash: float,
    no_new_entry: bool = False,
    quality_score: float | None = None,
    skip_fast_fund_gate: bool = False,
    quality_note: str = '',
) -> tuple[bool, float]:
    """
    三层分级买入：
      蓝筹底仓 (score ≥ 7.0)  → 100% alloc
      成长趋势 (score 5.0–7.0) →  70% alloc
      热门赛道 (score 3.0–5.0) →  40% alloc

    Returns:
        (bought, updated_cash)
    """
    if no_new_entry:
        return False, cash

    # 组合熔断检查：回撤超10%时暂停所有新入场
    if is_circuit_breaker_active():
        cb_status = get_circuit_breaker_status()
        print(f"[{label}] {stock} {cb_status}，跳过入场")
        return False, cash

    if has_open_order(stock):
        print(f"[{label}] {stock} 已有未完成订单，跳过")
        return False, cash

    # 板块集中度预检：超限直接跳过，不等事后风控介入
    if not check_sector_concentration(stock, label):
        return False, cash

    if count_bucket_positions(bucket_name) >= cfg['max_pos']:
        print(f"[{label}] {stock} 已满{cfg['max_pos']}仓，跳过")
        return False, cash

    min_cash = initial_cash * CASH_RESERVE
    if cash <= min_cash:
        print(f"[{label}] {stock} 现金储备不足，跳过")
        return False, cash

    signal_reason, signal_note, signal_score = signal
    tier_score = float(quality_score if quality_score is not None else fund_sc)
    emotion_top = False
    emotion_reason = ''
    if bucket_name in ('shortterm', 'longterm'):
        em = emotion_phase(df)
        emotion_top = em.phase == EmotionPhase.TOP
        if emotion_top:
            emotion_reason = em.reason

    admission = score_entry_candidate(
        bucket_name=bucket_name,
        signal_score=signal_score,
        quality_score=tier_score,
        fund_sc=fund_sc,
        skip_fast_fund_gate=skip_fast_fund_gate,
        vol_sig=vol_sig,
        emotion_top=emotion_top,
    )
    if admission.action == 'reject':
        extra = f" | 情绪:{emotion_reason}" if emotion_reason else ''
        print(
            f"[{label}] {stock} 综合评分{admission.total:.0f}不足，跳过"
            f" ({'; '.join(admission.notes)}){extra}"
        )
        return False, cash

    # 确定分层
    if tier_score >= 7.0:
        quality_alloc_mult = 1.0
        tier_label  = f"蓝筹底仓(质量{tier_score:.1f})"
    elif tier_score >= 5.0:
        quality_alloc_mult = 0.7
        tier_label  = f"成长趋势(质量{tier_score:.1f})"
    else:
        quality_alloc_mult = 0.4
        tier_label  = f"热门赛道(质量{tier_score:.1f})"

    decision_label = {
        'full': '标准仓',
        'scaled': '轻仓',
        'probe': '试探仓',
    }.get(admission.action, admission.action)
    alloc_mult = quality_alloc_mult * admission.alloc_mult

    # 综合入场缩放：讨论热度 × 市场状态 × 板块相关性
    entry_scale, scale_note = compute_entry_scale(stock)

    alloc_cash = entry_budget(
        cash, initial_cash, cfg['alloc'],
        signal_reason, reserve_ratio=CASH_RESERVE,
    ) * alloc_mult * entry_scale

    qty = atr_position_qty(price, atr_val, alloc_cash, reason=signal_reason)
    if qty <= 0:
        print(f"[{label}] {stock} 预算不足，跳过")
        return False, cash

    extra_note = (
        f"{tier_label} | {decision_label} | 综合分{admission.total:.0f}"
        f" | {ENTRY_REASON_LABEL.get(signal_reason, signal_reason)}({signal_note})"
        f" | 基本面{fund_sc:.0f}/10({fund_notes[0] if fund_notes else ''})"
        f"{f' | {quality_note}' if quality_note else ''}"
        f"{f' | 情绪顶部({emotion_reason})' if emotion_reason else ''}"
        f"{f' | 缩放×{entry_scale:.2f}({scale_note})' if scale_note else ''}"
        f" | 决策:{'; '.join(admission.notes)}"
        f" | {vol_note} | ATR={atr_val:.2f}"
    )

    ok, exec_p, msg = execute_buy(
        ctx, stock, qty, price, bucket_name, signal_reason, label,
        extra_note=f"{ind_str} | {extra_note}",
    )

    if ok:
        cash = broker.get_available_cash()
        return True, cash

    return False, cash
