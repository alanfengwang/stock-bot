"""
fetch_futu_data.py — 从 Futu OpenD 下载历史 K 线并保存为本地 CSV

用法：
  python3 fetch_futu_data.py                        # 下载全部股票，默认拉 400 根日线
  python3 fetch_futu_data.py --stocks NVDA,AMD      # 只下载指定股票
  python3 fetch_futu_data.py --bars 600             # 指定拉取根数（含 buffer）

输出：historical_data/<SYMBOL>_D.csv
CSV 格式：time_key,open,high,low,close,volume

之后运行回测：
  python3 backtest.py --local --from 2025-01-01 --to 2025-06-30
"""

from __future__ import annotations

import os
import sys
import time

import pandas as pd
from moomoo import AuType, KLType, OpenQuoteContext, RET_OK

from strategy_config import BUCKETS, WEEKLY_DCA_PLAN

BASE    = os.path.dirname(__file__)
OUT_DIR = os.path.join(BASE, 'historical_data')

# ── 命令行参数 ────────────────────────────────────────────────
def _get_flag(name: str) -> str | None:
    argv = sys.argv[1:]
    try:
        return argv[argv.index(name) + 1]
    except (ValueError, IndexError):
        return None

ONLY_STOCKS = _get_flag('--stocks')
N_BARS      = int(_get_flag('--bars') or 400)   # 默认 400 根（约 1.5 年日线）

# ── 股票池 ────────────────────────────────────────────────────
def all_stocks() -> list[str]:
    codes: list[str] = []
    for cfg in BUCKETS.values():
        codes.extend(cfg['stocks'])
    codes.extend(WEEKLY_DCA_PLAN.keys())
    # 去重，保持顺序
    return list(dict.fromkeys(codes))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    stocks = all_stocks()
    if ONLY_STOCKS:
        requested = [s.upper().strip() for s in ONLY_STOCKS.split(',')]
        # 支持带前缀（US.NVDA）和不带（NVDA）两种写法
        stocks = [
            s for s in stocks
            if s.replace('US.', '') in requested or s in requested
        ]
        if not stocks:
            # 如果不在已知池里，直接用用户输入
            stocks = [
                f'US.{s}' if not s.startswith('US.') else s
                for s in requested
            ]

    print(f"\n📦 Futu OpenD 历史数据下载")
    print(f"   股票数量：{len(stocks)}")
    print(f"   拉取根数：{N_BARS} 根日线（含 buffer）")
    print(f"   输出目录：{OUT_DIR}\n")

    ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
    ok = fail = 0

    for code in stocks:
        symbol = code.replace('US.', '')
        try:
            ret, df, _ = ctx.request_history_kline(
                code,
                ktype=KLType.K_DAY,
                autype=AuType.QFQ,
                max_count=N_BARS,
            )
            if ret != RET_OK or df is None or len(df) == 0:
                print(f"  ⚠️  {symbol:8s} 数据获取失败，跳过")
                fail += 1
                continue

            # 标准化列名（moomoo 返回的列有时叫 open/high/low/close，有时带前缀）
            df = df.rename(columns={
                'open':   'open',
                'high':   'high',
                'low':    'low',
                'close':  'close',
                'volume': 'volume',
            })

            out_cols = ['time_key', 'open', 'high', 'low', 'close', 'volume']
            out_cols = [c for c in out_cols if c in df.columns]
            df = df[out_cols].copy()

            csv_path = os.path.join(OUT_DIR, f'{symbol}_D.csv')
            df.to_csv(csv_path, index=False)

            # 显示数据时间范围
            t_start = str(df['time_key'].iloc[0])[:10]
            t_end   = str(df['time_key'].iloc[-1])[:10]
            print(f"  ✅  {symbol:8s}  {len(df)} 根  {t_start} → {t_end}")
            ok += 1

        except Exception as e:
            print(f"  ❌  {symbol:8s} 异常：{e}")
            fail += 1

        # Futu OpenD 有频率限制，稍微降速
        time.sleep(0.15)

    ctx.close()
    print(f"\n完成：{ok} 只成功，{fail} 只失败")
    print(f"\n下一步运行回测：")
    print(f"  python3 backtest.py --local --from 2025-01-01 --to 2025-06-30\n")


if __name__ == '__main__':
    main()
