"""
build_micro.py — 跨板块智能微量建仓

逻辑：
  1. 从 moomoo 拉取宇宙股票快照（夜盘/盘前实时价）
  2. 对每只股票进行综合评分（基本面 + 52周位置 + 今日动量）
  3. 先保证行业覆盖，再挑 5-10 只分散底仓
  4. 每只按统一底仓规则建仓（默认约 $500），跳过已持仓
  5. 干跑（DRY_RUN=True）时只打印计划，不实际下单

用法：
  python3 build_micro.py           # 干跑，查看计划
  python3 build_micro.py --execute # 实际建仓
"""

from __future__ import annotations
import sys, os
from typing import cast
import pandas as pd
from moomoo import OpenQuoteContext, RET_OK

from execution_policy import (
    CASH_RESERVE_RATIO,
    cash_position_qty,
    entry_budget,
)
from local_broker import LocalBroker
from market_utils import live_price_from_row
from micro_portfolio import score_snapshot_row, select_diversified_micro_candidates
from strategy_config import (
    MICRO_REENTRY_COOLDOWN_MINUTES,
    MICRO_ALLOC,
    MICRO_MAX_POS,
    MICRO_MIN_POS,
    MICRO_MIN_SCORE,
    MICRO_SECTOR_CAP,
    MICRO_SECTOR_CAP_OVERRIDES,
    MICRO_REQUIRED_SECTORS,
    MICRO_SECTOR_UNIVERSE,
    MICRO_TARGET_POS,
)
from trade_costs import calc_commission

# ── 路径 ──────────────────────────────────────────────────
BASE      = os.path.dirname(os.path.abspath(__file__))
BROKER_DB = os.path.join(BASE, 'virtual_account.json')
LOG_FILE  = os.path.join(BASE, 'trade_log.csv')

# ── 配置 ──────────────────────────────────────────────────
DRY_RUN = '--execute' not in sys.argv   # 默认干跑

# ── 股票宇宙（按板块）────────────────────────────────────
UNIVERSE = MICRO_SECTOR_UNIVERSE

ALL_STOCKS = [s for stocks in UNIVERSE.values() for s in stocks]

def get_live_price(r: pd.Series) -> float:
    return live_price_from_row(r)


# ── 主逻辑 ────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  跨板块微量建仓", "【干跑 - 不实际下单】" if DRY_RUN else "【实际下单】")
    print("=" * 60)

    # 1. 拉快照
    print(f"\n📡 连接 moomoo，拉取 {len(ALL_STOCKS)} 只股票快照...")
    ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
    ret, snap = ctx.get_market_snapshot(ALL_STOCKS)
    ctx.close()

    if ret != RET_OK:
        print("❌ 快照获取失败")
        return

    snap = cast(pd.DataFrame, snap)
    snap_map = {str(r['code']): r for _, r in snap.iterrows()}
    price_map = {
        code: get_live_price(row)
        for code, row in snap_map.items()
    }

    # 2. 评分
    scored: dict[str, float] = {}
    for code in ALL_STOCKS:
        if code in snap_map:
            scored[code] = score_snapshot_row(snap_map[code])

    # 3. 按板块选股
    broker  = LocalBroker(BROKER_DB, LOG_FILE, initial_cash=1_000_000.0)
    state   = broker.get_state()
    held    = set(state['positions'].keys())
    micro_held = {
        code for code, pos in state['positions'].items()
        if pos.get('bucket') == 'micro'
    }
    cash    = state['cash']
    initial = state['initial_cash']
    min_cash = initial * CASH_RESERVE_RATIO
    plan_cash = cash

    print(f"\n💰 当前现金: ${cash:,.0f}  (可用: ${max(0, cash-min_cash):,.0f})")
    print(f"📦 已持仓: {len(held)} 只\n")
    print(f"{'板块':<12} {'股票':<8} {'评分':>5}  {'现价':>8}  {'建议':>6}  {'原因'}")
    print("-" * 60)

    buy_plan: list[dict] = []
    target_new = max(0, min(MICRO_TARGET_POS, MICRO_MAX_POS) - len(micro_held))
    max_new = max(0, MICRO_MAX_POS - len(micro_held))
    selection_universe = {
        sector: [
            code for code in stocks
            if 0 < price_map.get(code, 0.0) <= MICRO_ALLOC
        ]
        for sector, stocks in UNIVERSE.items()
    }
    diversified = select_diversified_micro_candidates(
        scored,
        selection_universe,
        held,
        target_positions=target_new,
        max_positions=max_new,
        sector_cap=MICRO_SECTOR_CAP,
        min_score=MICRO_MIN_SCORE,
        sector_caps=MICRO_SECTOR_CAP_OVERRIDES,
        required_sectors=MICRO_REQUIRED_SECTORS,
    )
    diversified = [
        row for row in diversified
        if not broker.was_sold_recently(row['code'], MICRO_REENTRY_COOLDOWN_MINUTES)
    ]
    diversified_codes = {row['code']: row for row in diversified}

    for sector, stocks in UNIVERSE.items():
        skipped = [(c, scored.get(c, 0)) for c in stocks if c in held]
        for c, sc in skipped:
            price = price_map.get(c, 0.0)
            print(f"  {sector:<10} {c.replace('US.',''):<8} {sc:>5.1f}  ${price:>7.2f}  {'持仓中':>6}")

        sector_picks = [
            row for row in diversified
            if row['sector'] == sector
        ]
        for row in sector_picks:
            code = row['code']
            sc = row['score']
            if code not in snap_map:
                continue
            price = price_map.get(code, 0.0)
            if price <= 0:
                continue
            budget = entry_budget(
                plan_cash, initial, 0.0, 'micro_position',
                reserve_ratio=CASH_RESERVE_RATIO,
            )
            qty = cash_position_qty(price, budget)
            if qty <= 0:
                print(f"  {sector:<10} {code.replace('US.',''):<8} {sc:>5.1f}  ${price:>7.2f}  {'跳过':>6}  单价高于底仓预算")
                continue
            cost = qty * price
            comm = calc_commission(price, qty)
            if plan_cash - cost - comm < min_cash:
                print(f"  {sector:<10} {code.replace('US.',''):<8} {sc:>5.1f}  ${price:>7.2f}  {'现金不足':>6}")
                continue

            buy_plan.append({'code': code, 'sector': sector,
                             'score': sc, 'price': price, 'qty': qty,
                             'cost': cost, 'commission': comm})
            plan_cash -= cost + comm
            print(f"  {sector:<10} {code.replace('US.',''):<8} {sc:>5.1f}  ${price:>7.2f}  {'✅ 买入':>6}  "
                  f"{qty}股 ≈ ${cost:.0f}")

        # 低分的也打印出来供参考
        low = [(c, scored.get(c, 0)) for c in stocks
               if c not in held and c not in diversified_codes and scored.get(c, 0) < MICRO_MIN_SCORE]
        for c, sc in low:
            price = price_map.get(c, 0.0)
            print(f"  {sector:<10} {c.replace('US.',''):<8} {sc:>5.1f}  ${price:>7.2f}  {'跳过':>6}  评分不足")

        other = [(c, scored.get(c, 0)) for c in stocks
                 if c not in held and c not in diversified_codes and scored.get(c, 0) >= MICRO_MIN_SCORE]
        for c, sc in other:
            price = price_map.get(c, 0.0)
            note = '分散优先'
            if broker.was_sold_recently(c, MICRO_REENTRY_COOLDOWN_MINUTES):
                note = '卖出后冷静期'
            if price > MICRO_ALLOC:
                note = '单价高于底仓预算'
            print(f"  {sector:<10} {c.replace('US.',''):<8} {sc:>5.1f}  ${price:>7.2f}  {'跳过':>6}  {note}")

    # 4. 汇总计划
    total_cost = sum(p['cost'] + p['commission'] for p in buy_plan)
    print("\n" + "=" * 60)
    print(f"  目标底仓：{MICRO_MIN_POS}-{MICRO_MAX_POS} 只（当前默认目标 {MICRO_TARGET_POS} 只）")
    print(f"  建仓计划：{len(buy_plan)} 只  合计 ${total_cost:,.0f}")
    print(f"  执行后剩余现金：${plan_cash:,.0f}")
    print("=" * 60)

    if not buy_plan:
        print("无符合条件的建仓标的。")
        return

    if DRY_RUN:
        print("\n💡 这是干跑。加 --execute 参数实际下单：")
        print("   python3 build_micro.py --execute")
        return

    # 5. 实际下单
    print("\n🚀 开始下单...")
    success = 0
    for p in buy_plan:
        code  = p['code']
        price = p['price']
        qty   = p['qty']
        ok, msg = broker.place_order(
            code, 'BUY', qty, price,
            bucket='micro', reason='micro_position')
        if ok:
            print(f"  ✅ {code:14s} {qty}股 @ ${price:.2f}  {msg}")
            success += 1
        else:
            print(f"  ❌ {code:14s} 下单失败: {msg}")

    final = broker.get_state()
    print(f"\n完成：{success} 笔  剩余现金 ${final['cash']:,.0f}")


if __name__ == '__main__':
    main()
