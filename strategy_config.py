"""
strategy_config.py — 统一策略配置与共享常量

这里放：
1. 各桶参数
2. 研究/模拟共用常量
3. 股票宇宙与行业映射
"""

from __future__ import annotations

from moomoo import KLType

from execution_policy import CASH_RESERVE_RATIO, ENTRY_SIZE_POLICY

INITIAL_CASH = 1_000_000.0
CASH_RESERVE = CASH_RESERVE_RATIO

CRASH_THRESHOLD = -0.08
CRASH_RSI_ALERT = 25

MICRO_ALLOC = float(ENTRY_SIZE_POLICY['micro_position']['cash'])
MICRO_TARGET_POS = 8
MICRO_MIN_POS = 5
MICRO_MAX_POS = 10
MICRO_SECTOR_CAP = 1
MICRO_SECTOR_CAP_OVERRIDES = {
    'AI软件/云': 2,
}
MICRO_REQUIRED_SECTORS = ('太空国防',)
MICRO_MIN_SCORE = 6.5
DYNAMIC_INTERVAL = 1800

ADD_MIN_PROFIT = 0.05
ADD_MIN_DAYS = 7
ADD_RSI_MAX = 65

# ── 分批建仓（Pyramid Entry）参数 ─────────────────────────
PYRAMID_ADD1_PROFIT = 0.05   # +5%  触发第二批加仓
PYRAMID_ADD1_DAYS   = 7      # 最短持仓天数（第二批）
PYRAMID_ADD2_PROFIT = 0.12   # +12% 触发第三批加仓
PYRAMID_ADD2_DAYS   = 14     # 最短持仓天数（第三批）

# ── 分批止盈（Tiered Profit-Taking）参数 ──────────────────
PROFIT_TAKE1_PCT   = 0.08    # +8%  止盈第一批（约 30% 仓位）
PROFIT_TAKE2_PCT   = 0.15    # +15% 止盈第二批（约 40% 仓位）
# 剩余约 30% 仓位由移动止损（trail_stop）管理

RISK_PER_TRADE = 500.0
ATR_STOP_MULT = 2.0
BACKTEST_SLIPPAGE_BPS = 5.0
BACKTEST_LOOKBACK_BUFFER = 60

BUCKET_LABEL = {
    'conservative': '保守',
    'longterm': '成长',
    'shortterm': '短线',
    'micro': '底仓',
}
BUCKET_ORDER = ['conservative', 'longterm', 'shortterm', 'micro']

BUCKETS: dict[str, dict] = {
    'conservative': {
        'label': '保守',
        'stocks': [
            'US.AAPL', 'US.MSFT', 'US.GOOGL', 'US.META',
            'US.AVGO', 'US.ORCL', 'US.AMZN',
            'US.V', 'US.MA', 'US.DELL',          # 扩充：金融蓝筹 + AI基础设施
        ],
        'max_pos': 5,       # 放宽：原3，底仓观察模式
        'alloc': 0.18,
        'stop_loss': 0.08,
        'fast_ma': 10,
        'slow_ma': 50,
        'ktype': KLType.K_DAY,
        'backtest_ktype': KLType.K_DAY,
        'interval': 3600,
        'rsi_period': 14,
        'rsi_buy': 70,     # 放宽：原55，模拟仓允许在RSI<70时买入
        'rsi_sell': 78,
        'add_rsi_max': 65,
        'entry_modes': ('golden_cross', 'trend_pullback'),
        'pullback_lookback': 4,
        'pullback_band': 0.012,
        'pullback_reclaim_tol': 0.005,
        'starter_ratio': 0.40,
    },
    'longterm': {
        'label': '成长',
        'stocks': [
            'US.NVDA', 'US.AMD', 'US.MU', 'US.AMAT',
            'US.MRVL', 'US.KLAC', 'US.LRCX', 'US.ARM', 'US.QCOM',
            'US.WDC', 'US.STX',                   # 存储：HDD/NAND
            'US.CIEN', 'US.COHR',                 # 光模块龙头
        ],
        'max_pos': 6,       # 放宽：原4，底仓观察模式
        'alloc': 0.10,
        'stop_loss': 0.07,
        'fast_ma': 5,
        'slow_ma': 20,
        'ktype': KLType.K_DAY,
        'backtest_ktype': KLType.K_DAY,
        'interval': 1800,
        'macd_fast': 12,
        'macd_slow': 26,
        'macd_signal': 9,
        'entry_modes': ('golden_cross', 'trend_pullback', 'breakout'),
        'pullback_lookback': 5,
        'pullback_band': 0.015,
        'pullback_reclaim_tol': 0.006,
        'breakout_lookback': 20,
        'breakout_buffer': 0.002,
        'breakout_vol_mult': 1.0,   # 放宽：不再要求放量突破
        'starter_ratio': 0.35,
    },
    'shortterm': {
        'label': '短线',
        'stocks': [
            'US.PLTR', 'US.APP', 'US.NOW', 'US.CRWD',
            'US.DDOG', 'US.PANW', 'US.SMCI',
            'US.KTOS', 'US.RKLB', 'US.LUNR',
            'US.VST', 'US.CEG', 'US.NRG',
            'US.FCX', 'US.MP',
            'US.LITE', 'US.VIAV',               # 光模块（Lumentum / Viavi）
            'US.CCJ',                            # 铀矿/核能（Cameco）
            'US.AXON',                           # 警务/执法科技
        ],
        'max_pos': 5,       # 放宽：原3，底仓观察模式
        'alloc': 0.06,
        'stop_loss': 0.05,
        'fast_ma': 5,
        'slow_ma': 20,
        'ktype': KLType.K_5M,
        # 回测仍默认用日线近似，避免短线桶在本地请求超长 5 分钟数据时失真过大。
        'backtest_ktype': KLType.K_DAY,
        'interval': 300,
        'vol_period': 20,
        'vol_mult': 1.2,       # 放宽：原1.5，模拟仓只需超过均量20%
        'add_vol_mult': 1.0,   # 加码/回踩确认不强制要求放量
        'entry_modes': ('golden_cross', 'trend_pullback', 'breakout'),
        'pullback_lookback': 4,
        'pullback_band': 0.010,
        'pullback_reclaim_tol': 0.003,
        'breakout_lookback': 15,
        'breakout_buffer': 0.001,
        'breakout_vol_mult': 1.0,   # 放宽：不再要求放量突破
        'starter_ratio': 0.35,
    },
}

SECTOR_GROUPS: dict[str, list[str]] = {
    '大型科技': ['US.AAPL', 'US.MSFT', 'US.GOOGL', 'US.META', 'US.AMZN', 'US.TSLA', 'US.NFLX', 'US.UBER', 'US.ABNB', 'US.DELL'],
    'AI芯片': ['US.NVDA', 'US.AMD', 'US.MU', 'US.AMAT', 'US.MRVL', 'US.KLAC', 'US.LRCX', 'US.ARM', 'US.QCOM', 'US.AVGO', 'US.INTC', 'US.TXN', 'US.ASML', 'US.MCHP', 'US.ON', 'US.SWKS'],
    '存储': ['US.MU', 'US.WDC', 'US.STX'],
    '光模块': ['US.CIEN', 'US.COHR', 'US.LITE', 'US.VIAV'],
    'AI软件': ['US.PLTR', 'US.APP', 'US.NOW', 'US.CRWD', 'US.DDOG', 'US.PANW', 'US.SMCI', 'US.ORCL', 'US.CRM', 'US.SNOW', 'US.MDB', 'US.GTLB', 'US.ZS', 'US.NET', 'US.HUBS', 'US.BILL'],
    '金融科技': ['US.V', 'US.MA', 'US.PYPL', 'US.SQ', 'US.COIN', 'US.HOOD'],
    '太空国防': ['US.KTOS', 'US.RKLB', 'US.LUNR', 'US.LMT', 'US.RTX', 'US.NOC', 'US.AXON'],
    '电力能源': ['US.VST', 'US.CEG', 'US.NRG', 'US.NEE', 'US.AES', 'US.CCJ'],
    '金属矿物': ['US.FCX', 'US.MP', 'US.VALE', 'US.NEM'],
    '医疗/生物': ['US.LLY', 'US.MRNA', 'US.ABBV', 'US.TMO'],
    '消费/零售': ['US.COST', 'US.WMT', 'US.TGT', 'US.SBUX'],
}
SECTOR_ORDER = list(SECTOR_GROUPS.keys())
SECTOR_MAP = {
    code: sector
    for sector, stocks in SECTOR_GROUPS.items()
    for code in stocks
}

UNIVERSE: list[str] = list(SECTOR_MAP.keys())

MICRO_SECTOR_UNIVERSE: dict[str, list[str]] = {
    '大型科技': ['US.TSLA', 'US.NFLX', 'US.UBER', 'US.ABNB'],
    'AI芯片': ['US.AMD', 'US.QCOM', 'US.INTC', 'US.TXN', 'US.ON'],
    'AI软件/云': ['US.CRM', 'US.NOW', 'US.SNOW', 'US.MDB', 'US.NET', 'US.ZS', 'US.GTLB'],
    '金融科技': ['US.V', 'US.MA', 'US.PYPL', 'US.COIN', 'US.HOOD'],
    '太空国防': ['US.RKLB', 'US.KTOS', 'US.LMT', 'US.RTX'],
    '电力能源': ['US.NEE', 'US.AES', 'US.NRG'],
    '金属矿物': ['US.FCX', 'US.MP', 'US.VALE', 'US.NEM'],
    '医疗/生物': ['US.LLY', 'US.MRNA', 'US.ABBV', 'US.TMO'],
    '消费/零售': ['US.COST', 'US.WMT', 'US.TGT', 'US.SBUX'],
}


def bucket_stocks(bucket_names: list[str] | None = None) -> list[str]:
    names = bucket_names or list(BUCKETS.keys())
    return sorted({
        stock
        for bucket_name in names
        for stock in BUCKETS[bucket_name]['stocks']
    })
