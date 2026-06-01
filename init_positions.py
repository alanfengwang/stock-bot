"""
init_positions.py — 一次性建仓脚本
分析 portfolio_bot 全部股票池，按 基本面 + 动量 + 52周位置 综合评分，
每桶选最优 TOP_N 只，以 INIT_ALLOC 仓位建小仓。
"""
from moomoo import *
import pandas as pd
from typing import cast
import os

from market_utils import live_price_from_row
from screener import fundamental_score
from local_broker import LocalBroker
from strategy_config import BUCKETS

# ── 参数 ──────────────────────────────────────────
INIT_ALLOC   = 0.02    # 每只占总资产 2%（建仓试水）
TOP_N        = 2       # 每桶取前 N 只
BASE         = os.path.dirname(__file__)
BROKER_DB    = os.path.join(BASE, 'virtual_account.json')
LOG_FILE     = os.path.join(BASE, 'trade_log.csv')

BUCKETS_DEF = {
    name: {'label': cfg['label'], 'stocks': list(cfg['stocks'])}
    for name, cfg in BUCKETS.items()
}
# ──────────────────────────────────────────────────


def current_price(snap_row: pd.Series) -> float:
    """
    返回当前最真实的可交易价格。
    优先级：夜盘(overnight) > 盘前(pre) > 盘后(after) > 收盘(last)
    夜盘是正在活跃交易的会话，数据最新。
    """
    return live_price_from_row(snap_row)

def score_stock(snap_row: pd.Series, df_day: pd.DataFrame | None) -> dict:
    """三维评分：基本面 40% + 动量 35% + 52周位置 25%"""
    last   = float(snap_row['last_price'])
    high52 = float(snap_row['highest52weeks_price'])
    low52  = float(snap_row['lowest52weeks_price'])

    # 基本面
    fund_sc, fund_notes = fundamental_score(snap_row)

    # 20 日价格动量
    if df_day is not None and len(df_day) >= 21:
        p20 = float(df_day['close'].iloc[-21])
        momentum = (last - p20) / p20 if p20 else 0
    else:
        momentum = 0
    mom_sc = max(0.0, min(10.0, 5 + momentum * 40))

    # 52 周位置（偏好 25%–65% 区间，不追高也不抄底）
    w52_range = high52 - low52
    w52_pos   = (last - low52) / w52_range if w52_range > 0 else 0.5
    if 0.25 <= w52_pos <= 0.65:
        pos_sc = 10.0
    elif 0.10 <= w52_pos < 0.25:
        pos_sc = 7.0    # 偏低，可能仍在下行
    elif 0.65 < w52_pos <= 0.80:
        pos_sc = 6.0    # 偏高，谨慎
    elif w52_pos > 0.80:
        pos_sc = 3.0    # 接近年高，追高风险
    else:
        pos_sc = 5.0

    total = fund_sc * 0.40 + mom_sc * 0.35 + pos_sc * 0.25

    buy_p = current_price(snap_row)   # 盘前/隔夜 > 收盘

    return {
        'price':      buy_p,          # 用于实际下单
        'ref_price':  last,           # 用于评分参考（收盘价更稳定）
        'score':      round(total, 2),
        'fund_sc':    fund_sc,
        'fund_notes': fund_notes,
        'mom_sc':     round(mom_sc, 1),
        'momentum':   momentum,
        'pos_sc':     round(pos_sc, 1),
        'w52_pos':    w52_pos,
    }

def main():
    broker    = LocalBroker(BROKER_DB, LOG_FILE, initial_cash=1_000_000.0)
    quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)

    state        = broker.get_state()
    total_assets = state['initial_cash']
    cash         = state['cash']
    print(f"账户总资产: ${total_assets:>12,.0f}")
    print(f"当前现金:   ${cash:>12,.0f}  ({cash/total_assets*100:.1f}%)")

    # 批量拉快照
    all_stocks = list({s for b in BUCKETS_DEF.values() for s in b['stocks']})
    ret, snap  = quote_ctx.get_market_snapshot(all_stocks)
    if ret != RET_OK:
        print(f"快照失败: {snap}")
        return
    snap = cast(pd.DataFrame, snap)
    snap_map = {str(r['code']): r for _, r in snap.iterrows()}

    # 逐股分析
    ranked: dict[str, list] = {b: [] for b in BUCKETS_DEF}

    for bucket, cfg in BUCKETS_DEF.items():
        print(f"\n{'─'*60}")
        print(f"  [{cfg['label']}桶] 分析中...")
        print(f"{'─'*60}")

        for stock in cfg['stocks']:
            if stock not in snap_map:
                continue
            snap_row = snap_map[stock]

            ret_k, df, _ = quote_ctx.request_history_kline(
                stock, ktype=KLType.K_DAY, autype=AuType.QFQ, max_count=30)
            df = cast(pd.DataFrame, df)
            df_day = df if ret_k == RET_OK and len(df) >= 10 else None

            s = score_stock(snap_row, df_day)
            w52_str  = f"{s['w52_pos']*100:.0f}%"
            mom_str  = f"{s['momentum']*100:+.1f}%"
            note_str = s['fund_notes'][0] if s['fund_notes'] else ''

            print(f"  {stock:12s} ${s['price']:>9.2f}  "
                  f"综合:{s['score']:4.1f}  "
                  f"基本面:{s['fund_sc']:.0f}  "
                  f"动量:{mom_str:>7s}  "
                  f"52w位:{w52_str:>4s}  "
                  f"({note_str})")

            ranked[bucket].append({'stock': stock, 'bucket': bucket,
                                   'label': cfg['label'], **s})

    # 每桶取 TOP_N
    to_buy = []
    for bucket, rows in ranked.items():
        rows.sort(key=lambda x: x['score'], reverse=True)
        to_buy.extend(rows[:TOP_N])

    # 建仓计划
    print(f"\n{'═'*60}")
    print(f"  建仓计划  (每只 {INIT_ALLOC*100:.0f}% ≈ ${total_assets*INIT_ALLOC:,.0f})")
    print(f"{'═'*60}")
    plan_rows = []
    for r in to_buy:
        qty  = max(1, int(total_assets * INIT_ALLOC / r['price']))
        cost = qty * r['price']
        plan_rows.append((r, qty, cost))
        print(f"  [{r['label']:2s}] {r['stock']:12s} "
              f"{qty:4d}股 × ${r['price']:8.2f} = ${cost:>10,.0f}"
              f"  (评分:{r['score']:.1f})")

    total_cost = sum(c for _, _, c in plan_rows)
    remain     = cash - total_cost
    print(f"{'─'*60}")
    print(f"  合计投入: ${total_cost:>10,.0f}  "
          f"剩余现金: ${remain:>10,.0f}  ({remain/total_assets*100:.1f}%)")

    if remain < 0:
        print("⚠️  现金不足，请减少 INIT_ALLOC 或 TOP_N")
        return

    # 执行下单
    print(f"\n{'═'*60}")
    print("  开始建仓（本地虚拟撮合）...")
    print(f"{'═'*60}")
    success, fail = 0, 0

    for r, qty, _ in plan_rows:
        ok, msg = broker.place_order(
            r['stock'], 'BUY', qty, r['price'],
            bucket=r['bucket'], reason='init_position')
        if ok:
            print(f"  ✅ [{r['label']}] {r['stock']} {msg}")
            success += 1
        else:
            print(f"  ❌ {r['stock']} 失败: {msg}")
            fail += 1

    final = broker.get_state()
    print(f"\n完成：{success} 笔成功，{fail} 笔失败")
    print(f"剩余现金：${final['cash']:,.2f}")
    quote_ctx.close()

if __name__ == '__main__':
    main()
