"""
market_regime.py — 市场状态分类 · 信号评分 · 板块动量门控 · 情绪顶底

融合自 daily_stock_analysis 项目的策略思想，适配美股自动交易系统。

四个核心模块：
  1. MarketRegime      — 市场状态分类（trending_up / sideways / volatile / sector_hot / trending_down）
  2. SignalScore       — 信号评分制（替代二元 yes/no，输出 -100~+100 综合分）
  3. SectorMomentum   — 板块动量门控（进仓前检查板块强弱）
  4. EmotionSentinel  — 情绪顶底判断（量能周期，美股适配版）

用法示例：
    from market_regime import classify_regime, score_signal, sector_gate, emotion_phase

    regime = classify_regime(spy_df, qqq_df, vix=18.5)
    score  = score_signal(df, event_type='breakout', regime=regime)
    ok     = sector_gate(sector_df_map, sector='AI芯片', regime=regime)
    phase  = emotion_phase(df)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd

from strategy_signals import calc_rsi, calc_macd, calc_atr_value, calc_volume_ratio


# ══════════════════════════════════════════════════════════════════
# 1. 市场状态分类
# ══════════════════════════════════════════════════════════════════

class Regime(str, Enum):
    TRENDING_UP   = 'trending_up'    # 趋势上行：大盘多头，最适合买入
    SECTOR_HOT    = 'sector_hot'     # 板块热点：大盘一般但特定板块强势
    SIDEWAYS      = 'sideways'       # 横盘震荡：箱体操作，降低仓位
    VOLATILE      = 'volatile'       # 高波动：VIX 高企，缩小仓位或观望
    TRENDING_DOWN = 'trending_down'  # 趋势下行：空仓或做空


@dataclass
class RegimeResult:
    regime: Regime
    reason: str
    ma_aligned: bool        # SPY MA20 > MA50 > MA200
    vix_level: str          # 'low' / 'elevated' / 'high' / 'extreme'
    breadth_score: float    # 0~1，上涨股票比例代理值
    score: float            # 综合市场得分 -100 ~ +100


def classify_regime(
    spy_df: pd.DataFrame,
    qqq_df: Optional[pd.DataFrame] = None,
    vix: Optional[float] = None,
    sector_leaders: Optional[list[str]] = None,
) -> RegimeResult:
    """
    根据 SPY 日线数据（必须）+ 可选 QQQ / VIX 值，分类当前市场状态。

    参数：
        spy_df          : SPY 日线 DataFrame，列：open/high/low/close/volume
        qqq_df          : QQQ 日线（可选，用于 Nasdaq 确认）
        vix             : 当前 VIX 值（可选，若无则用 SPY ATR 代理）
        sector_leaders  : 近期强势板块列表（可选，用于判断 sector_hot）

    返回 RegimeResult
    """
    if len(spy_df) < 200:
        return RegimeResult(
            regime=Regime.SIDEWAYS,
            reason='数据不足 200 根，无法可靠分类',
            ma_aligned=False, vix_level='unknown',
            breadth_score=0.5, score=0.0,
        )

    close = spy_df['close']
    ma20  = float(close.rolling(20).mean().iloc[-1])
    ma50  = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1])
    cur   = float(close.iloc[-1])

    ma_aligned = (cur > ma20 > ma50 > ma200)
    ma_score   = _ma_score(cur, ma20, ma50, ma200)

    # ── VIX / 波动率 ──────────────────────────────────────────────
    if vix is not None:
        vix_val = vix
    else:
        # 用 SPY 近 20 日 ATR/收盘 作为 VIX 代理
        atr = calc_atr_value(spy_df.tail(30))
        vix_val = atr / cur * 100 * 10   # 粗略映射到 VIX 量纲

    vix_level, vix_penalty = _vix_classify(vix_val)

    # ── 趋势斜率（近 20 日收益率）────────────────────────────────
    ret_20 = (cur - float(close.iloc[-20])) / float(close.iloc[-20]) * 100
    trend_score = max(-40.0, min(40.0, ret_20 * 2))

    # ── QQQ 确认 ─────────────────────────────────────────────────
    qqq_confirm = 0.0
    if qqq_df is not None and len(qqq_df) >= 50:
        qc = qqq_df['close']
        q_ma20 = float(qc.rolling(20).mean().iloc[-1])
        q_ma50 = float(qc.rolling(50).mean().iloc[-1])
        q_cur  = float(qc.iloc[-1])
        if q_cur > q_ma20 > q_ma50:
            qqq_confirm = 10.0
        elif q_cur < q_ma20 < q_ma50:
            qqq_confirm = -10.0

    # ── 面包度（breadth）用 RSI 代理 ─────────────────────────────
    rsi_spy = float(calc_rsi(close).iloc[-1])
    breadth_score = rsi_spy / 100.0

    # ── 综合评分 ─────────────────────────────────────────────────
    total = ma_score + trend_score + qqq_confirm - vix_penalty
    total = max(-100.0, min(100.0, total))

    # ── 分类判断 ─────────────────────────────────────────────────
    if vix_level in ('high', 'extreme'):
        regime = Regime.VOLATILE
        reason = f'VIX={vix_val:.1f} 高波动，缩仓观望'
    elif total >= 30 and ma_aligned:
        regime = Regime.TRENDING_UP
        reason = f'多头排列 MA20>{ma20:.0f}>MA50>{ma50:.0f}>MA200>{ma200:.0f}'
    elif total <= -30:
        regime = Regime.TRENDING_DOWN
        reason = f'大盘趋势向下，20日收益{ret_20:.1f}%'
    elif sector_leaders and len(sector_leaders) >= 2:
        regime = Regime.SECTOR_HOT
        reason = f'板块轮动热点：{", ".join(sector_leaders[:3])}'
    else:
        regime = Regime.SIDEWAYS
        reason = f'横盘震荡，MA20={ma20:.0f} / MA50={ma50:.0f}'

    return RegimeResult(
        regime=regime, reason=reason,
        ma_aligned=ma_aligned, vix_level=vix_level,
        breadth_score=breadth_score, score=total,
    )


def _ma_score(cur: float, ma20: float, ma50: float, ma200: float) -> float:
    score = 0.0
    if cur > ma20:   score += 15
    if ma20 > ma50:  score += 15
    if ma50 > ma200: score += 20
    if cur < ma20:   score -= 15
    if ma20 < ma50:  score -= 15
    if ma50 < ma200: score -= 20
    return score


def _vix_classify(vix: float) -> tuple[str, float]:
    if vix < 15:   return 'low',     0.0
    if vix < 20:   return 'normal',  0.0
    if vix < 28:   return 'elevated', 10.0
    if vix < 35:   return 'high',    30.0
    return 'extreme', 50.0


# ══════════════════════════════════════════════════════════════════
# 2. 信号评分制
# ══════════════════════════════════════════════════════════════════

@dataclass
class SignalScore:
    event_type: str          # 原信号类型（golden_cross / breakout 等）
    base_score: float        # 技术面基础分
    regime_adj: float        # 市场状态调整
    volume_adj: float        # 量能调整
    bias_adj: float          # 乖离率调整（借鉴 DSA 反追高机制）
    macd_adj: float          # MACD 调整
    total: float             # 综合分 -100 ~ +100
    tradeable: bool          # total >= threshold 才可交易
    reasons: list[str] = field(default_factory=list)


# 各信号类型基础分（来自 DSA 策略思想）
_BASE_SCORES: dict[str, float] = {
    'golden_cross':    10.0,   # MA 金叉，经典但滞后
    'trend_pullback':  12.0,   # 缩量回踩，DSA 最偏爱的买点
    'breakout':        14.0,   # 放量突破，DSA volume_breakout 核心
    'uptrend':          8.0,   # 趋势确认底仓
    'momentum_surge':  10.0,   # 动量加速
    'rsi_bounce':       9.0,   # RSI 超卖反弹
    'bb_breakout':     11.0,   # 布林带突破
    '52w_high':        13.0,   # 52 周新高，机构行为确认
    'macd_zero_cross': 12.0,   # MACD 零轴上穿
    'premarket_gap':    7.0,   # 盘前异动，风险较高
}

# 市场状态对各信号的乘数
_REGIME_MULT: dict[Regime, float] = {
    Regime.TRENDING_UP:   1.3,
    Regime.SECTOR_HOT:    1.1,
    Regime.SIDEWAYS:      0.7,
    Regime.VOLATILE:      0.5,
    Regime.TRENDING_DOWN: 0.2,
}

# 乖离率阈值（DSA 默认 5%，美股强势股放宽）
_BIAS_THRESHOLDS: dict[str, float] = {
    'conservative': 0.06,   # 蓝筹宽松一点
    'longterm':     0.08,   # AI/芯片高 beta 再放宽
    'shortterm':    0.10,   # 短线追动量，允许更高乖离
    'default':      0.07,
}


def score_signal(
    df: pd.DataFrame,
    event_type: str,
    regime: Optional[RegimeResult] = None,
    bucket: str = 'default',
    fast_ma_period: int = 10,
    threshold: float = 12.0,
) -> SignalScore:
    """
    对一个已触发的信号打分，决定是否值得交易。

    参数：
        df          : 个股日线 DataFrame
        event_type  : 信号类型（与 execution_policy 的 key 一致）
        regime      : 市场状态（None 则不调整）
        bucket      : 所属桶，影响乖离率阈值
        fast_ma_period : 快线周期（用于计算乖离率）
        threshold   : 总分达到此值才 tradeable

    返回 SignalScore
    """
    reasons: list[str] = []
    base = _BASE_SCORES.get(event_type, 8.0)

    # ── 市场状态调整 ─────────────────────────────────────────────
    if regime is not None:
        mult = _REGIME_MULT.get(regime.regime, 1.0)
        regime_adj = base * (mult - 1.0)
        reasons.append(f'市场={regime.regime.value}({mult:.1f}x)')
    else:
        regime_adj = 0.0

    # ── 量能调整（参考 DSA volume_breakout / emotion_cycle）──────
    vol_ratio = calc_volume_ratio(df, 20)
    if vol_ratio >= 2.0:
        volume_adj = 8.0
        reasons.append(f'放量{vol_ratio:.1f}x(+8)')
    elif vol_ratio >= 1.5:
        volume_adj = 4.0
        reasons.append(f'量能{vol_ratio:.1f}x(+4)')
    elif vol_ratio < 0.7 and event_type == 'trend_pullback':
        volume_adj = 5.0   # 缩量回踩是好事
        reasons.append(f'缩量回踩{vol_ratio:.1f}x(+5)')
    elif vol_ratio < 0.5:
        volume_adj = -5.0
        reasons.append(f'量能萎缩{vol_ratio:.1f}x(-5)')
    else:
        volume_adj = 0.0

    # ── 乖离率调整（DSA 反追高机制，ATR 适配版）─────────────────
    bias_threshold = _BIAS_THRESHOLDS.get(bucket, _BIAS_THRESHOLDS['default'])
    close = df['close']
    fast_ma = float(close.rolling(fast_ma_period).mean().iloc[-1])
    cur_price = float(close.iloc[-1])
    bias = (cur_price - fast_ma) / fast_ma if fast_ma > 0 else 0.0

    if bias > bias_threshold * 1.5:
        bias_adj = -15.0
        reasons.append(f'严重追高乖离{bias*100:.1f}%(-15)')
    elif bias > bias_threshold:
        bias_adj = -8.0
        reasons.append(f'乖离偏高{bias*100:.1f}%(-8)')
    elif bias < -0.03:
        bias_adj = -5.0
        reasons.append(f'价格弱于均线{bias*100:.1f}%(-5)')
    else:
        bias_adj = 0.0

    # ── MACD 调整（DSA 金叉/零轴加分）───────────────────────────
    macd_adj = 0.0
    if len(df) >= 35 and 'macd' in df.columns:
        m = float(df['macd'].iloc[-1])
        m_prev = float(df['macd'].iloc[-2])
        s = float(df['macd_sig'].iloc[-1]) if 'macd_sig' in df.columns else 0.0
        if m > 0 and m > s:
            macd_adj = 5.0
            reasons.append('MACD零轴上方(+5)')
        elif m_prev < 0 and m > 0:
            macd_adj = 8.0
            reasons.append('MACD零轴上穿(+8)')
        elif m < s and m < 0:
            macd_adj = -6.0
            reasons.append('MACD零轴下方(-6)')

    total = base + regime_adj + volume_adj + bias_adj + macd_adj
    total = max(-100.0, min(100.0, total))

    return SignalScore(
        event_type=event_type,
        base_score=base,
        regime_adj=regime_adj,
        volume_adj=volume_adj,
        bias_adj=bias_adj,
        macd_adj=macd_adj,
        total=total,
        tradeable=total >= threshold,
        reasons=reasons,
    )


# ══════════════════════════════════════════════════════════════════
# 3. 板块动量门控
# ══════════════════════════════════════════════════════════════════

@dataclass
class SectorGateResult:
    passed: bool
    sector: str
    sector_ret_5d: float     # 板块近 5 日收益率（代理：用板块代表股）
    sector_vs_spy: float     # 板块相对 SPY 超额收益
    rank_pct: float          # 在所有板块中的排名百分位（0=最弱，1=最强）
    reason: str


def sector_gate(
    sector_df_map: dict[str, pd.DataFrame],
    sector: str,
    spy_df: Optional[pd.DataFrame] = None,
    top_pct: float = 0.5,
    lookback: int = 5,
) -> SectorGateResult:
    """
    板块动量门控：只有板块处于前 top_pct 强才放行。

    参数：
        sector_df_map : {板块名: 该板块代表ETF/股票的日线DF}
                        例如 {'AI芯片': soxl_df, '大型科技': qqq_df, ...}
        sector        : 当前要检查的板块名
        spy_df        : SPY 日线（用于计算相对强弱），可选
        top_pct       : 前多少比例的板块才通过（0.5 = 前 50%）
        lookback      : 计算收益率的回看天数

    返回 SectorGateResult
    """
    if sector not in sector_df_map:
        return SectorGateResult(
            passed=True, sector=sector,
            sector_ret_5d=0.0, sector_vs_spy=0.0,
            rank_pct=0.5, reason='无板块数据，默认通过',
        )

    def _ret(df: pd.DataFrame, n: int) -> float:
        if len(df) < n + 1:
            return 0.0
        c = df['close']
        return float((c.iloc[-1] - c.iloc[-n]) / c.iloc[-n])

    # 当前板块收益
    target_ret = _ret(sector_df_map[sector], lookback)

    # SPY 对比
    spy_ret = _ret(spy_df, lookback) if spy_df is not None else 0.0
    vs_spy = target_ret - spy_ret

    # 全部板块排名
    all_rets = {s: _ret(df, lookback) for s, df in sector_df_map.items()}
    sorted_rets = sorted(all_rets.values())
    if len(sorted_rets) > 1:
        rank_idx = sorted_rets.index(target_ret)
        rank_pct = rank_idx / (len(sorted_rets) - 1)
    else:
        rank_pct = 0.5

    passed = rank_pct >= (1.0 - top_pct)

    reason = (
        f'{sector} {lookback}日涨{target_ret*100:.1f}% '
        f'vs SPY {spy_ret*100:.1f}% '
        f'(板块排名前{(1-rank_pct)*100:.0f}%)'
    )

    return SectorGateResult(
        passed=passed, sector=sector,
        sector_ret_5d=target_ret, sector_vs_spy=vs_spy,
        rank_pct=rank_pct, reason=reason,
    )


# ══════════════════════════════════════════════════════════════════
# 4. 情绪顶底判断（DSA emotion_cycle 美股适配版）
# ══════════════════════════════════════════════════════════════════

class EmotionPhase(str, Enum):
    BOTTOM    = 'bottom'     # 情绪底部：冷淡低量，逆向布局区
    WARMING   = 'warming'    # 情绪升温：量价启动，可介入
    NORMAL    = 'normal'     # 正常区间：无明显信号
    HEATING   = 'heating'    # 过热预警：追高风险上升
    TOP       = 'top'        # 情绪顶部：狂热放量，减仓区


@dataclass
class EmotionResult:
    phase: EmotionPhase
    score: int               # 满足特征的条目数（负=顶部特征，正=底部特征）
    vol_ratio_now: float     # 当前量比（vs 20日均）
    vol_ratio_trend: str     # 'rising' / 'falling' / 'flat'
    rsi_now: float
    bias_from_ma20: float    # 价格偏离 MA20 百分比
    reason: str
    bottom_signals: list[str]
    top_signals: list[str]


def emotion_phase(
    df: pd.DataFrame,
    lookback_vol: int = 20,
    rsi_period: int = 14,
) -> EmotionResult:
    """
    情绪周期判断（美股适配）。

    DSA 用换手率判断，美股用 volume_ratio（成交量/N日均量）代替。
    阈值针对美股调整：
      - 美股日均换手率普遍低于 A 股，volume_ratio 阈值更保守
      - RSI 超卖阈值沿用 30/35，超买用 70/75

    底部特征（满足 3+ 项 → BOTTOM）：
      ① 近 20 日量比均值处于近一年低位（量能萎缩）
      ② 当前量比 < 0.6（极度冷淡）
      ③ RSI < 35（超卖区）
      ④ 价格在 MA20 附近或下方（-3% ~ +2%）
      ⑤ 近 5 日量能持续下降（情绪退潮）

    顶部特征（满足 3+ 项 → TOP）：
      ① 近 5 日量比均值 > 近 20 日的 2 倍（加速放量）
      ② 当前量比 > 2.5（单日爆量）
      ③ RSI > 72（超买区）
      ④ 价格偏离 MA20 超过 8%（高乖离）
      ⑤ MACD 出现顶背离（价格新高但 MACD 不创新高）
    """
    if len(df) < max(lookback_vol, rsi_period) + 10:
        return EmotionResult(
            phase=EmotionPhase.NORMAL, score=0,
            vol_ratio_now=1.0, vol_ratio_trend='flat',
            rsi_now=50.0, bias_from_ma20=0.0,
            reason='数据不足', bottom_signals=[], top_signals=[],
        )

    close = df['close']
    volume = df['volume'] if 'volume' in df.columns else pd.Series([1.0] * len(df))

    # ── 基础指标 ──────────────────────────────────────────────────
    cur_price = float(close.iloc[-1])
    ma20      = float(close.rolling(20).mean().iloc[-1])
    bias      = (cur_price - ma20) / ma20 if ma20 > 0 else 0.0

    rsi_ser   = calc_rsi(close, rsi_period)
    rsi_now   = float(rsi_ser.iloc[-1])

    vol_ma20  = float(volume.rolling(lookback_vol).mean().iloc[-1])
    vol_now   = float(volume.iloc[-1])
    vol_ratio = vol_now / vol_ma20 if vol_ma20 > 0 else 1.0

    # 近 5 日量比趋势
    vol_5d_ratios = [
        float(volume.iloc[-i]) / vol_ma20 if vol_ma20 > 0 else 1.0
        for i in range(1, 6)
    ]
    vol_5d_avg = sum(vol_5d_ratios) / len(vol_5d_ratios)

    # 历史量比均值（近 60 日）
    if len(volume) >= 60:
        vol_hist_avg = float(volume.iloc[-60:].mean() / volume.iloc[-60:].rolling(20).mean().mean())
    else:
        vol_hist_avg = 1.0

    # 量比趋势
    if vol_5d_ratios[0] > vol_5d_ratios[-1] * 1.2:
        vol_trend = 'rising'
    elif vol_5d_ratios[0] < vol_5d_ratios[-1] * 0.8:
        vol_trend = 'falling'
    else:
        vol_trend = 'flat'

    # MACD 顶背离（价格新高但 MACD 不创新高）
    macd_line, _ = calc_macd(close)
    macd_diverge = False
    if len(close) >= 30:
        price_new_high  = float(close.iloc[-1]) >= float(close.iloc[-20:].max())
        macd_new_high   = float(macd_line.iloc[-1]) >= float(macd_line.iloc[-20:].max())
        macd_diverge    = price_new_high and not macd_new_high

    # ── 底部特征检测 ───────────────────────────────────────────────
    bottom_signals: list[str] = []
    if vol_5d_avg < vol_hist_avg * 0.6:
        bottom_signals.append(f'近5日量比{vol_5d_avg:.2f}处于历史低位')
    if vol_ratio < 0.6:
        bottom_signals.append(f'当前量比{vol_ratio:.2f}极度冷淡')
    if rsi_now < 35:
        bottom_signals.append(f'RSI={rsi_now:.0f}超卖')
    if -0.05 <= bias <= 0.02:
        bottom_signals.append(f'价格贴近MA20(乖离{bias*100:.1f}%)')
    if vol_trend == 'falling' and vol_5d_avg < 0.8:
        bottom_signals.append('近5日量能持续萎缩')

    # ── 顶部特征检测 ───────────────────────────────────────────────
    top_signals: list[str] = []
    if vol_5d_avg > 2.0:
        top_signals.append(f'近5日量比均值{vol_5d_avg:.2f}(过热)')
    if vol_ratio > 2.5:
        top_signals.append(f'当前量比{vol_ratio:.2f}爆量')
    if rsi_now > 72:
        top_signals.append(f'RSI={rsi_now:.0f}超买')
    if bias > 0.08:
        top_signals.append(f'价格偏离MA20达{bias*100:.1f}%(追高风险)')
    if macd_diverge:
        top_signals.append('MACD顶背离')

    # ── 综合判断 ───────────────────────────────────────────────────
    n_bottom = len(bottom_signals)
    n_top    = len(top_signals)

    if n_bottom >= 4:
        phase = EmotionPhase.BOTTOM
        reason = f'情绪底部({n_bottom}/5项)：逆向布局区'
    elif n_bottom >= 3:
        phase = EmotionPhase.WARMING
        reason = f'情绪趋冷({n_bottom}/5项)：可小仓试探'
    elif n_top >= 4:
        phase = EmotionPhase.TOP
        reason = f'情绪顶部({n_top}/5项)：减仓警戒区'
    elif n_top >= 3:
        phase = EmotionPhase.HEATING
        reason = f'情绪过热({n_top}/5项)：追高风险上升'
    else:
        phase = EmotionPhase.NORMAL
        reason = f'情绪正常区(底{n_bottom}/顶{n_top}项)'

    net_score = n_bottom - n_top

    return EmotionResult(
        phase=phase,
        score=net_score,
        vol_ratio_now=vol_ratio,
        vol_ratio_trend=vol_trend,
        rsi_now=rsi_now,
        bias_from_ma20=bias,
        reason=reason,
        bottom_signals=bottom_signals,
        top_signals=top_signals,
    )


# ══════════════════════════════════════════════════════════════════
# 5. 综合入场过滤器（将以上四个模块串联）
# ══════════════════════════════════════════════════════════════════

@dataclass
class EntryFilter:
    """综合入场过滤结果，供 portfolio_bot / bucket_runner 调用"""
    allowed: bool
    signal_score: SignalScore
    regime: Optional[RegimeResult]
    sector_gate: Optional[SectorGateResult]
    emotion: EmotionResult
    summary: str


def entry_filter(
    df: pd.DataFrame,
    event_type: str,
    bucket: str = 'default',
    spy_df: Optional[pd.DataFrame] = None,
    qqq_df: Optional[pd.DataFrame] = None,
    vix: Optional[float] = None,
    sector_df_map: Optional[dict[str, pd.DataFrame]] = None,
    sector: Optional[str] = None,
    score_threshold: float = 12.0,
    block_on_top: bool = True,
) -> EntryFilter:
    """
    一站式入场过滤器，整合四个模块。

    参数：
        df              : 个股日线 DataFrame
        event_type      : 信号类型
        bucket          : 所属桶
        spy_df          : SPY 日线（市场状态判断用）
        qqq_df          : QQQ 日线（可选）
        vix             : 当前 VIX（可选）
        sector_df_map   : 板块代表 ETF 日线映射（可选）
        sector          : 个股所属板块（可选）
        score_threshold : 信号评分及格线
        block_on_top    : 情绪顶部时强制拦截

    返回 EntryFilter
    """
    # 1. 市场状态
    regime = classify_regime(spy_df, qqq_df, vix) if spy_df is not None else None

    # 2. 信号评分
    ss = score_signal(df, event_type, regime=regime, bucket=bucket)

    # 3. 板块门控
    sg = None
    if sector_df_map and sector:
        sg = sector_gate(sector_df_map, sector, spy_df=spy_df)

    # 4. 情绪判断
    em = emotion_phase(df)

    # ── 综合决策 ─────────────────────────────────────────────────
    blocks: list[str] = []

    if not ss.tradeable:
        blocks.append(f'信号评分{ss.total:.0f}<{score_threshold}')

    if sg and not sg.passed:
        blocks.append(f'板块门控未通过({sg.reason})')

    if block_on_top and em.phase == EmotionPhase.TOP:
        blocks.append(f'情绪顶部拦截({em.reason})')

    if regime and regime.regime == Regime.TRENDING_DOWN:
        blocks.append(f'大盘下行趋势({regime.reason})')

    if regime and regime.regime == Regime.VOLATILE:
        blocks.append(f'VIX高波动({regime.reason})')

    allowed = len(blocks) == 0

    parts = [f'信号={event_type}', f'评分={ss.total:.0f}']
    if regime:
        parts.append(f'市场={regime.regime.value}')
    if sg:
        parts.append(f'板块{"✓" if sg.passed else "✗"}')
    parts.append(f'情绪={em.phase.value}')
    if blocks:
        parts.append(f'拦截:{"|".join(blocks)}')

    return EntryFilter(
        allowed=allowed,
        signal_score=ss,
        regime=regime,
        sector_gate=sg,
        emotion=em,
        summary=' | '.join(parts),
    )
