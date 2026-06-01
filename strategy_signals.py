"""
strategy_signals.py — 共享指标与事件检测
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class IndicatorState:
    ind_str: str
    extra_buy: bool
    extra_sell: bool
    relaxed_buy: bool
    rsi_now: float | None = None
    volume_ratio: float | None = None


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, float('nan')))


def calc_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    return macd, macd.ewm(span=signal, adjust=False).mean()


def calc_volume_ratio(df: pd.DataFrame, period: int = 20) -> float:
    if 'volume' not in df.columns or len(df) < period:
        return 1.0
    vol_ma = df['volume'].rolling(period).mean()
    base = float(vol_ma.iloc[-1]) if len(vol_ma) else 0.0
    cur = float(df['volume'].iloc[-1])
    return cur / base if base > 0 else 1.0


def calc_atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([
        (h - l),
        (h - c.shift()).abs(),
        (l - c.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calc_atr_value(df: pd.DataFrame, period: int = 14) -> float:
    atr = calc_atr_series(df, period)
    value = float(atr.iloc[-1])
    close = float(df['close'].iloc[-1])
    return value if value == value else close * 0.02


def enrich_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    out['fast_ma'] = out['close'].rolling(cfg['fast_ma']).mean()
    out['slow_ma_v'] = out['close'].rolling(cfg['slow_ma']).mean()
    if 'high' in out.columns and 'low' in out.columns:
        out['atr'] = calc_atr_series(out)
    if 'rsi_period' in cfg:
        out['rsi'] = calc_rsi(out['close'], cfg['rsi_period'])
    if 'macd_fast' in cfg:
        out['macd'], out['macd_sig'] = calc_macd(
            out['close'],
            cfg['macd_fast'],
            cfg['macd_slow'],
            cfg['macd_signal'],
        )
    if 'vol_period' in cfg:
        out['vol_ma'] = out['volume'].rolling(cfg['vol_period']).mean()
    return out


def indicator_state(bucket_name: str,
                    cfg: dict,
                    latest: pd.Series,
                    prev_row: pd.Series) -> IndicatorState:
    if bucket_name == 'conservative':
        rsi_now = float(latest.get('rsi', 50))
        return IndicatorState(
            ind_str=f"RSI={rsi_now:.0f}",
            extra_buy=rsi_now < cfg['rsi_buy'],
            extra_sell=rsi_now > cfg['rsi_sell'],
            relaxed_buy=rsi_now < cfg.get('add_rsi_max', 65),
            rsi_now=rsi_now,
        )

    if bucket_name == 'longterm':
        m = float(latest.get('macd', 0))
        s = float(latest.get('macd_sig', 0))
        mp = float(prev_row.get('macd', 0))
        sp = float(prev_row.get('macd_sig', 0))
        return IndicatorState(
            ind_str=f"MACD={m:.2f}/SIG={s:.2f}",
            extra_buy=m > s,
            extra_sell=mp > sp and m < s,
            relaxed_buy=m > s,
        )

    vol_period = int(cfg.get('vol_period', 20))
    vol_ma = float(latest.get('vol_ma', 0))
    volume_ratio = float(latest.get('volume', 0)) / max(vol_ma, 1)
    return IndicatorState(
        ind_str=f"量比={volume_ratio:.1f}x",
        extra_buy=volume_ratio >= cfg['vol_mult'],
        extra_sell=False,
        relaxed_buy=volume_ratio >= cfg.get('add_vol_mult', cfg['vol_mult']),
        volume_ratio=volume_ratio,
    )


def detect_uptrend(df: pd.DataFrame,
                   fast_now: float,
                   slow_now: float,
                   n: int = 3) -> tuple[str, str] | None:
    """
    蓝筹底仓入场：连续 N 根 K 线快线均在慢线上方。
    不要求具体形态（金叉/回踩），只确认整体趋势向上。
    """
    if fast_now <= slow_now or len(df) < n + 1:
        return None
    recent_fast = df['fast_ma'].iloc[-n:].dropna().values
    recent_slow = df['slow_ma_v'].iloc[-n:].dropna().values
    if len(recent_fast) < n or len(recent_slow) < n:
        return None
    if all(f > s for f, s in zip(recent_fast, recent_slow)):
        return 'uptrend', f"趋势确认({n}根快>慢线)"
    return None


def detect_entry_signal(cfg: dict,
                        df: pd.DataFrame,
                        latest: pd.Series,
                        prev_row: pd.Series,
                        fast_now: float,
                        slow_now: float,
                        fast_prev: float,
                        slow_prev: float,
                        signal_ok: bool) -> tuple[str, str] | None:
    """
    买入事件：
    1. golden_cross   新金叉
    2. trend_pullback 上升趋势中回踩快线后重新站上
    3. breakout       上升趋势中突破近期高点
    """
    entry_modes = set(cfg.get('entry_modes', ('golden_cross',)))
    price = float(latest['close'])
    ma_golden = fast_prev < slow_prev and fast_now > slow_now

    # 金叉是最基础信号，不要求附加指标确认
    if 'golden_cross' in entry_modes and ma_golden:
        return 'golden_cross', '新金叉'

    if fast_now <= slow_now:
        return None

    # 回踩信号：快线在慢线上方即可，不强制附加指标（模拟仓底仓观察模式）
    if 'trend_pullback' in entry_modes:
        lookback = int(cfg.get('pullback_lookback', 4))
        band = float(cfg.get('pullback_band', 0.012))
        reclaim_tol = float(cfg.get('pullback_reclaim_tol', 0.005))
        if len(df) >= lookback + 2:
            recent = df.iloc[-(lookback + 1):-1]
            low_col = 'low' if 'low' in recent.columns else 'close'
            recent_low = float(recent[low_col].min())
            prev_close = float(prev_row['close'])

            near_fast = recent_low <= fast_now * (1 + band)
            reclaim_ok = (
                prev_close <= fast_prev * (1 + reclaim_tol)
                and price > fast_now
                and price > prev_close
            )
            if near_fast and reclaim_ok:
                return 'trend_pullback', f"回踩{cfg['fast_ma']}MA后再站上"

    # 突破信号：同样不再强制 signal_ok
    if 'breakout' in entry_modes:
        lookback = int(cfg.get('breakout_lookback', 20))
        buffer = float(cfg.get('breakout_buffer', 0.002))
        vol_mult = float(cfg.get('breakout_vol_mult', 1.2))
        if len(df) >= lookback + 2:
            recent = df.iloc[-(lookback + 1):-1]
            high_col = 'high' if 'high' in recent.columns else 'close'
            recent_high = float(recent[high_col].max())
            volume_ratio = calc_volume_ratio(df, int(cfg.get('vol_period', 20)))
            if price >= recent_high * (1 + buffer) and volume_ratio >= vol_mult:
                return 'breakout', f"{lookback}bar突破 量比{volume_ratio:.1f}x"

    return None
