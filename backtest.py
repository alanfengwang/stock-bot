"""
backtest.py — 历史回测引擎

基于历史 K 线，重放共享策略逻辑。
说明：
1. 与 live 端共用事件检测、仓位规则、交易成本函数
2. 默认加入固定滑点模型（见 strategy_config.py）
3. 导出回测摘要与交易明细 CSV

用法：
  # 默认：连接 Futu OpenD 拉最近 252 个交易日
  python3 backtest.py
  python3 backtest.py conservative
  python3 backtest.py longterm 500

  # 本地 CSV 模式（先用 fetch_tv_data.js 下载数据）：
  python3 backtest.py --local
  python3 backtest.py --local --from 2025-01-01 --to 2025-06-30
  python3 backtest.py --local conservative --from 2024-01-01

  python3 backtest.py --help
"""

from __future__ import annotations

import math
import os
import sys

import pandas as pd

from execution_policy import atr_position_qty, entry_budget, equity_pct_qty
from performance import calc_pnl_metrics, closed_trade_pnls
from strategy_config import (
    ADD_MIN_DAYS,
    ADD_MIN_PROFIT,
    ADD_RSI_MAX,
    BACKTEST_LOOKBACK_BUFFER,
    BACKTEST_SLIPPAGE_BPS,
    BUCKETS,
    CASH_RESERVE,
    INITIAL_CASH,
    PROFIT_TAKE1_PCT,
    PROFIT_TAKE2_PCT,
    PYRAMID_ADD1_DAYS,
    PYRAMID_ADD1_PROFIT,
    PYRAMID_ADD2_DAYS,
    PYRAMID_ADD2_PROFIT,
)
from strategy_signals import calc_atr_value, detect_entry_signal, enrich_indicators, indicator_state
from trade_costs import apply_slippage, calc_commission

# ── 本地 CSV 数据加载 ──────────────────────────────────────────
_LOCAL_DATA_DIR = os.path.join(os.path.dirname(__file__), 'historical_data')


def load_local_kline(
    code: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> pd.DataFrame | None:
    """
    从 historical_data/<SYMBOL>_D.csv 加载本地 K 线数据。
    CSV 格式（由 fetch_tv_data.js 生成）：
      time_key,open,high,low,close,volume
    """
    symbol = code.replace('US.', '').upper()
    csv_path = os.path.join(_LOCAL_DATA_DIR, f'{symbol}_D.csv')
    if not os.path.exists(csv_path):
        return None

    df = pd.read_csv(csv_path, parse_dates=['time_key'])
    df = df.sort_values('time_key').reset_index(drop=True)
    df['time_key'] = df['time_key'].astype(str).str[:10]  # 保持 YYYY-MM-DD 字符串

    if from_date:
        df = df[df['time_key'] >= from_date]
    if to_date:
        df = df[df['time_key'] <= to_date]

    return df.reset_index(drop=True) if len(df) > 0 else None


def _atr_from_row_or_value(df: pd.DataFrame, idx: int) -> float:
    atr_col = df.get('atr')
    if atr_col is not None:
        atr_val = float(atr_col.iloc[idx])
        if not math.isnan(atr_val):
            return atr_val
    return calc_atr_value(df.iloc[:idx + 1])


def backtest_stock(bucket_name: str,
                   code: str,
                   df: pd.DataFrame,
                   cfg: dict,
                   initial_cash: float) -> dict:
    """
    对单只股票重放策略。
    df 必须按时间升序，且包含 open/high/low/close/volume 列。
    """
    df = enrich_indicators(df.copy().reset_index(drop=True), cfg)
    min_bars = max(cfg['slow_ma'] + 5, 40)

    cash = initial_cash
    pos: dict | None = None
    trades: list[dict] = []
    trailing_high = 0.0
    profit_stages: set[int] = set()   # 已触发的止盈阶段 {1, 2}

    for i in range(min_bars, len(df)):
        history = df.iloc[:i + 1]
        latest = history.iloc[-1]
        prev_row = history.iloc[-2]

        fast_now = float(latest['fast_ma'])
        slow_now = float(latest['slow_ma_v'])
        fast_prev = float(prev_row['fast_ma'])
        slow_prev = float(prev_row['slow_ma_v'])
        if any(math.isnan(x) for x in [fast_now, slow_now, fast_prev, slow_prev]):
            continue

        raw_price = float(latest['close'])
        atr_val = _atr_from_row_or_value(df, i)
        ma_death = fast_prev > slow_prev and fast_now < slow_now
        date_str = str(latest.get('time_key', i))

        ind_state = indicator_state(bucket_name, cfg, latest, prev_row)
        entry_signal = detect_entry_signal(
            cfg, history, latest, prev_row,
            fast_now, slow_now, fast_prev, slow_prev,
            ind_state.extra_buy,
        )

        if pos is not None:
            qty = int(pos['qty'])
            entry_price = float(pos['entry_price'])
            pnl_pct = (raw_price - entry_price) / entry_price if entry_price else 0.0
            trailing_high = max(trailing_high, raw_price)
            trail_stop = trailing_high - atr_val * 1.5

            # 分批止盈 阶段 1：盈利 ≥ +8% → 卖出约 30%
            if pnl_pct >= PROFIT_TAKE1_PCT and 1 not in profit_stages and qty >= 3:
                take_qty = max(1, round(qty * 0.30))
                fill_price = apply_slippage(raw_price, 'SELL', BACKTEST_SLIPPAGE_BPS)
                commission = calc_commission(fill_price, take_qty)
                pnl_value = (fill_price - entry_price) * take_qty - commission
                cash += fill_price * take_qty - commission
                pos['qty'] -= take_qty
                profit_stages.add(1)
                trades.append({
                    'date': date_str,
                    'bucket': bucket_name,
                    'code': code,
                    'side': 'SELL_PARTIAL',
                    'price': fill_price,
                    'qty': take_qty,
                    'reason': 'profit_take1',
                    'pnl': pnl_value,
                })
                qty = int(pos['qty'])

            # 分批止盈 阶段 2：盈利 ≥ +15% → 再卖出约 60% 余仓（≈原仓位 40%）
            if pnl_pct >= PROFIT_TAKE2_PCT and 2 not in profit_stages and 1 in profit_stages and qty >= 2:
                take_qty = max(1, round(qty * 0.60))
                fill_price = apply_slippage(raw_price, 'SELL', BACKTEST_SLIPPAGE_BPS)
                commission = calc_commission(fill_price, take_qty)
                pnl_value = (fill_price - entry_price) * take_qty - commission
                cash += fill_price * take_qty - commission
                pos['qty'] -= take_qty
                profit_stages.add(2)
                trades.append({
                    'date': date_str,
                    'bucket': bucket_name,
                    'code': code,
                    'side': 'SELL_PARTIAL',
                    'price': fill_price,
                    'qty': take_qty,
                    'reason': 'profit_take2',
                    'pnl': pnl_value,
                })
                qty = int(pos['qty'])

            sell_reason = None
            if pnl_pct <= -cfg['stop_loss']:
                sell_reason = 'stop_loss'
            elif raw_price < trail_stop and pnl_pct > 0.03:
                sell_reason = 'trailing_stop'
            elif ma_death and (bucket_name != 'longterm' or ind_state.extra_sell):
                sell_reason = 'death_cross'
            elif ind_state.extra_sell and bucket_name == 'conservative':
                sell_reason = 'rsi_overbought'

            if sell_reason:
                fill_price = apply_slippage(raw_price, 'SELL', BACKTEST_SLIPPAGE_BPS)
                commission = calc_commission(fill_price, qty)
                pnl_value = (fill_price - entry_price) * qty - commission
                cash += fill_price * qty - commission
                trades.append({
                    'date': date_str,
                    'bucket': bucket_name,
                    'code': code,
                    'side': 'SELL',
                    'price': fill_price,
                    'qty': qty,
                    'reason': sell_reason,
                    'pnl': pnl_value,
                })
                pos = None
                trailing_high = 0.0
                profit_stages = set()
                continue

            bars_held = i - int(pos['entry_bar'])
            rsi_ok = (
                ind_state.rsi_now is None
                or ind_state.rsi_now < cfg.get('add_rsi_max', ADD_RSI_MAX)
            )
            min_cash = initial_cash * CASH_RESERVE

            # 分批建仓 阶段 2：盈利 ≥ +5%，持仓 ≥ 7 根 K 线
            if (
                pos['add_count'] < 1
                and pnl_pct >= PYRAMID_ADD1_PROFIT
                and bars_held >= PYRAMID_ADD1_DAYS
                and fast_now > slow_now
                and rsi_ok
            ):
                # 金字塔第二批：加仓 30% 原始分配额（Vibe-Trading 权重式）
                add_qty = equity_pct_qty(
                    raw_price, initial_cash,
                    cfg.get('position_pct', 0.60) * 0.30,
                )
                # 现金不足时缩减到可用最大股数
                _max_add = int((cash - initial_cash * CASH_RESERVE) / max(raw_price, 0.01))
                add_qty = min(add_qty, max(_max_add, 0))
                if add_qty > 0:
                    fill_price = apply_slippage(raw_price, 'BUY', BACKTEST_SLIPPAGE_BPS)
                    cost = fill_price * add_qty
                    commission = calc_commission(fill_price, add_qty)
                    if cash - cost - commission >= min_cash:
                        new_qty = qty + add_qty
                        new_avg = ((entry_price * qty) + (fill_price * add_qty)) / new_qty
                        cash -= cost + commission
                        pos['qty'] = new_qty
                        pos['entry_price'] = new_avg
                        pos['add_count'] += 1
                        trades.append({
                            'date': date_str,
                            'bucket': bucket_name,
                            'code': code,
                            'side': 'BUY',
                            'price': fill_price,
                            'qty': add_qty,
                            'reason': 'pyramid_stage2',
                            'pnl': 0.0,
                        })

            # 分批建仓 阶段 3：盈利 ≥ +12%，持仓 ≥ 14 根 K 线
            elif (
                pos['add_count'] == 1
                and pnl_pct >= PYRAMID_ADD2_PROFIT
                and bars_held >= PYRAMID_ADD2_DAYS
                and fast_now > slow_now
            ):
                # 金字塔第三批：加仓 20% 原始分配额（Vibe-Trading 权重式）
                add_qty = equity_pct_qty(
                    raw_price, initial_cash,
                    cfg.get('position_pct', 0.60) * 0.20,
                )
                _max_add = int((cash - initial_cash * CASH_RESERVE) / max(raw_price, 0.01))
                add_qty = min(add_qty, max(_max_add, 0))
                if add_qty > 0:
                    fill_price = apply_slippage(raw_price, 'BUY', BACKTEST_SLIPPAGE_BPS)
                    cost = fill_price * add_qty
                    commission = calc_commission(fill_price, add_qty)
                    if cash - cost - commission >= min_cash:
                        new_qty = qty + add_qty
                        new_avg = ((entry_price * qty) + (fill_price * add_qty)) / new_qty
                        cash -= cost + commission
                        pos['qty'] = new_qty
                        pos['entry_price'] = new_avg
                        pos['add_count'] += 1
                        trades.append({
                            'date': date_str,
                            'bucket': bucket_name,
                            'code': code,
                            'side': 'BUY',
                            'price': fill_price,
                            'qty': add_qty,
                            'reason': 'pyramid_stage3',
                            'pnl': 0.0,
                        })

        elif entry_signal is not None:
            signal_reason, signal_note = entry_signal
            # Vibe-Trading 权重式仓位：用 per-stock 分配额的 position_pct 建仓
            qty = equity_pct_qty(
                raw_price, initial_cash,
                cfg.get('position_pct', 0.60),
            )
            if qty <= 0:
                continue

            fill_price = apply_slippage(raw_price, 'BUY', BACKTEST_SLIPPAGE_BPS)
            cost = fill_price * qty
            commission = calc_commission(fill_price, qty)
            min_cash = initial_cash * CASH_RESERVE
            # 现金不足时缩减股数至可用上限
            if cash - cost - commission < min_cash:
                max_affordable = int((cash - min_cash - commission) / max(fill_price, 0.01))
                if max_affordable <= 0:
                    continue
                qty = max_affordable
                cost = fill_price * qty
            if cash - cost - commission < min_cash:
                continue

            cash -= cost + commission
            pos = {
                'qty': qty,
                'entry_price': fill_price,
                'entry_bar': i,
                'add_count': 0,
            }
            trailing_high = raw_price
            profit_stages = set()
            trades.append({
                'date': date_str,
                'bucket': bucket_name,
                'code': code,
                'side': 'BUY',
                'price': fill_price,
                'qty': qty,
                'reason': signal_reason,
                'note': signal_note,
                'pnl': 0.0,
            })

    if pos is not None:
        final_price = float(df.iloc[-1]['close'])
        qty = int(pos['qty'])
        fill_price = apply_slippage(final_price, 'SELL', BACKTEST_SLIPPAGE_BPS)
        commission = calc_commission(fill_price, qty)
        pnl_value = (fill_price - float(pos['entry_price'])) * qty - commission
        cash += fill_price * qty - commission
        trades.append({
            'date': str(df.iloc[-1].get('time_key', len(df))),
            'bucket': bucket_name,
            'code': code,
            'side': 'SELL',
            'price': fill_price,
            'qty': qty,
            'reason': 'end_of_backtest',
            'pnl': pnl_value,
        })

    return {
        'bucket': bucket_name,
        'code': code,
        'trades': trades,
        'final_cash': cash,
    }


def _write_reports(summary_rows: list[dict], trade_rows: list[dict]) -> tuple[str, str]:
    base = os.path.dirname(__file__)
    summary_path = os.path.join(base, 'backtest_summary.csv')
    trades_path = os.path.join(base, 'backtest_trades.csv')
    pd.DataFrame(summary_rows).to_csv(summary_path, index=False)
    pd.DataFrame(trade_rows).to_csv(trades_path, index=False)
    return summary_path, trades_path


def run_backtest(
    bucket_names: list[str],
    n_periods: int,
    local: bool = False,
    from_date: str | None = None,
    to_date: str | None = None,
):
    # ── 数据源选择 ──────────────────────────────────────────────
    if local:
        ctx = None
        data_src = f"本地 CSV ({_LOCAL_DATA_DIR})"
        if from_date or to_date:
            data_src += f"  {from_date or '起始'} → {to_date or '最新'}"
    else:
        from moomoo import OpenQuoteContext
        from market_utils import request_kline
        ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
        data_src = "Futu OpenD"

    print(f"\n{'=' * 60}")
    print(f"  回测引擎 | 数据源：{data_src}")
    print(f"  期间 {n_periods} 根 | 桶：{', '.join(bucket_names)}")
    print(f"  初始资金 ${INITIAL_CASH:,.0f} | 现金储备 {CASH_RESERVE * 100:.0f}% | 滑点 {BACKTEST_SLIPPAGE_BPS:.1f} bps")
    print(f"{'=' * 60}\n")
    print("注：回测不重放 snapshot 基本面，只重放价格/均线/量能与仓位逻辑。\n")

    summary_rows: list[dict] = []
    trade_rows: list[dict] = []

    def _get_df(code: str) -> pd.DataFrame | None:
        if local:
            df = load_local_kline(code, from_date, to_date)
            if df is None:
                return None
            # 本地模式：保留足够的 buffer 行（fetch_tv_data.js 已多拉 70 根）
            return df if len(df) >= BACKTEST_LOOKBACK_BUFFER else None
        else:
            df = request_kline(
                ctx,
                code,
                cfg.get('backtest_ktype', cfg['ktype']),
                n_periods + BACKTEST_LOOKBACK_BUFFER,
            )
            return df if df is not None and len(df) >= BACKTEST_LOOKBACK_BUFFER else None

    try:
        for bucket_name in bucket_names:
            cfg = BUCKETS[bucket_name]
            label = cfg['label']
            stocks = cfg['stocks']

            print(f"─── [{label}桶] {len(stocks)} 只股票 ───────────────────────")

            per_stock_cash = INITIAL_CASH / len(stocks)
            results = []

            for code in stocks:
                df = _get_df(code)
                if df is None:
                    print(f"  {code:14s}  ⚠️  数据不足或文件缺失，跳过")
                    continue

                result = backtest_stock(
                    bucket_name,
                    code,
                    df.tail(n_periods + BACKTEST_LOOKBACK_BUFFER).reset_index(drop=True),
                    cfg,
                    per_stock_cash,
                )
                results.append(result)
                trade_rows.extend(result['trades'])

                pnls = closed_trade_pnls(result['trades'])
                stock_pnl = sum(pnls)
                sell_count = len([t for t in result['trades'] if t['side'] in ('SELL', 'SELL_HALF')])
                win_count = len([p for p in pnls if p > 0])
                ret_pct = stock_pnl / per_stock_cash * 100 if per_stock_cash else 0.0
                if sell_count:
                    print(
                        f"  {code:14s}  交易{sell_count:>3}笔  盈亏 ${stock_pnl:>+9,.0f}"
                        f"  收益率 {ret_pct:>+6.1f}%  胜率 {win_count / sell_count * 100:.0f}%"
                    )
                else:
                    print(f"  {code:14s}  无交易")

            if not results:
                print("  无有效数据\n")
                continue

            bucket_pnls = [
                pnl
                for result in results
                for pnl in closed_trade_pnls(result['trades'])
            ]
            tested_cash = per_stock_cash * len(results)
            metrics = calc_pnl_metrics(bucket_pnls, tested_cash, n_periods)

            summary_rows.append({
                'bucket': bucket_name,
                'label': label,
                'stocks_tested': len(results),
                **metrics,
            })

            print(f"\n  【{label}桶汇总】")
            print(
                f"  总交易：{metrics['total_trades']} 笔  胜率：{metrics['win_rate'] * 100:.1f}%"
            )
            print(
                f"  利润因子：{metrics['profit_factor']:.2f}  总盈亏：${metrics['total_pnl']:+,.0f}"
                f"  年化收益：{metrics['ann_ret'] * 100:+.1f}%"
            )
            print(
                f"  最大回撤：{metrics['max_dd'] * 100:.1f}%  Sharpe（近似）：{metrics['sharpe']:.2f}\n"
            )
    finally:
        if ctx is not None:
            ctx.close()

    summary_path, trades_path = _write_reports(summary_rows, trade_rows)
    print(f"{'=' * 60}")
    print(f"回测完成：")
    print(f"摘要: {summary_path}")
    print(f"明细: {trades_path}")


def _get_flag(name: str) -> str | None:
    """解析 --name value 形式的命令行参数。"""
    argv = sys.argv[1:]
    try:
        idx = argv.index(name)
        return argv[idx + 1]
    except (ValueError, IndexError):
        return None


def main():
    if '--help' in sys.argv:
        print(__doc__)
        return

    use_local  = '--local' in sys.argv
    from_date  = _get_flag('--from')
    to_date    = _get_flag('--to')

    # 过滤掉所有 flag 参数，只保留位置参数
    positional = [
        a for a in sys.argv[1:]
        if not a.startswith('--') and a not in (from_date or '', to_date or '')
    ]
    valid_buckets = list(BUCKETS.keys())
    bucket_names  = [a for a in positional if a in valid_buckets] or valid_buckets
    n_periods     = next((int(a) for a in positional if a.isdigit()), 125 if use_local else 252)

    run_backtest(bucket_names, n_periods, local=use_local, from_date=from_date, to_date=to_date)


if __name__ == '__main__':
    main()
