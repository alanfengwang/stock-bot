from moomoo import *
import time, os, threading, subprocess
from datetime import datetime
import pandas as pd
from typing import cast

from execution_policy import (
    ENTRY_REASON_LABEL,
    atr_position_qty,
    cash_position_qty,
    entry_budget,
    is_starter_position,
)
from exit_rules import evaluate_trailing_stop, holding_days, should_time_stop
from fundamental_model import score_slow_fundamentals
from fundamental_store import get_fundamental_entry, refresh_fundamental_cache
from market_utils import live_price_from_row, request_kline
from micro_portfolio import (
    score_snapshot_row,
    select_diversified_micro_candidates,
)
from discussion_universe import build_watch_universe, load_discussion_universe
from recurring_invest import (
    current_new_york_time,
    should_execute_weekly_dca,
    week_marker,
)
from screener import fundamental_score, volume_signal, MIN_FUND_SCORE
from local_broker import LocalBroker
from strategy_config import (
    ADD_MIN_DAYS,
    ADD_MIN_PROFIT,
    ADD_RSI_MAX,
    BUCKET_LABEL,
    BUCKETS,
    BASE_WATCH_UNIVERSE,
    CASH_RESERVE,
    CRASH_RSI_ALERT,
    CRASH_THRESHOLD,
    DISCUSSION_WATCH_LIMIT,
    DYNAMIC_INTERVAL,
    INITIAL_CASH,
    MICRO_ALLOC,
    MICRO_MAX_POS,
    MICRO_MIN_SCORE,
    MICRO_SECTOR_CAP,
    MICRO_SECTOR_CAP_OVERRIDES,
    MICRO_REQUIRED_SECTORS,
    MICRO_SECTOR_UNIVERSE,
    MICRO_TARGET_POS,
    PROFIT_TAKE1_PCT,
    PROFIT_TAKE2_PCT,
    PYRAMID_ADD1_DAYS,
    PYRAMID_ADD1_PROFIT,
    PYRAMID_ADD2_DAYS,
    PYRAMID_ADD2_PROFIT,
    REENTRY_COOLDOWN_MINUTES,
    MICRO_REENTRY_COOLDOWN_MINUTES,
    SECTOR_MAP,
    SLOW_FUND_MIN_SCORE,
    SLOW_FUND_REFRESH_INTERVAL,
    SLOW_FUND_TIER_FULL,
    SLOW_FUND_TIER_MID,
    TRADE_UNIVERSE,
    WEEKLY_DCA_INTERVAL,
    WEEKLY_DCA_MIN_HOUR_ET,
    WEEKLY_DCA_PLAN,
    WEEKLY_DCA_WEEKDAY_ET,
)
from strategy_signals import (
    calc_atr_value,
    calc_rsi,
    detect_entry_signal,
    detect_uptrend,
    detect_premarket_signal,
    detect_momentum_surge,
    enrich_indicators,
    indicator_state,
)

# ══════════════════════════════════════════════════════════
# 路径
# ══════════════════════════════════════════════════════════
BASE      = os.path.dirname(__file__)
BROKER_DB = os.path.join(BASE, 'virtual_account.json')
LOG_FILE  = os.path.join(BASE, 'trade_log.csv')
HEARTBEAT_INTERVAL = 60

# 动态 watchlist（由 run_dynamic_screener 维护）
_dynamic_lock  = threading.Lock()
_dynamic_watch: dict[str, float] = {}   # code → score

# ══════════════════════════════════════════════════════════
# 全局对象
# ══════════════════════════════════════════════════════════
broker = LocalBroker(BROKER_DB, LOG_FILE, initial_cash=INITIAL_CASH)


def runtime_position_snapshot(raw_pos: dict) -> dict:
    return {
        'bucket': str(raw_pos.get('bucket', '') or ''),
        'entry_price': float(raw_pos.get('avg_cost', raw_pos.get('entry_price', 0.0)) or 0.0),
        'qty': int(raw_pos.get('qty', 0) or 0),
        'entry_time': str(raw_pos.get('entry_time', '') or ''),
    }


def current_watch_universe() -> list[str]:
    return build_watch_universe(
        BASE_WATCH_UNIVERSE,
        load_discussion_universe(),
        extra_limit=DISCUSSION_WATCH_LIMIT,
    )

# 内存持仓缓存（从 broker 初始化）
_raw = broker.get_state()['positions']
positions: dict = {
    code: runtime_position_snapshot(p)
    for code, p in _raw.items()
}
positions_lock = threading.Lock()
BOT_START_TS = time.time()


def _restore_runtime_state():
    """从 broker 恢复内存状态（重启时调用）。"""
    global _profit_stages, _trailing_highs
    state = broker.get_state()
    for code, pos in state['positions'].items():
        stages = set(pos.get('profit_stages', []))
        if stages:
            _profit_stages[code] = stages
        trail = float(pos.get('trail_high', 0.0))
        if trail > 0:
            _trailing_highs[code] = trail

# ══════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════
# ── 市场状态（全局，由 run_regime_monitor 维护）─────────────
_regime      = 'BULL'       # 'BULL' | 'NEUTRAL' | 'BEAR'
_regime_lock = threading.Lock()

# ── 移动止损高水位（各股最高成本价跟踪）─────────────────────
_trailing_highs: dict[str, float] = {}
_trail_lock = threading.Lock()

# ── 分批止盈阶段标记（code → {1, 2} 已触发阶段）────────────
_profit_stages: dict[str, set[int]] = {}
_profit_lock = threading.Lock()

# ── 线程心跳状态（用于证明系统仍在运行）──────────────────────
_worker_beats: dict[str, dict[str, float | str]] = {}
_worker_lock = threading.Lock()

# 从 broker 恢复内存状态（重启时执行）
_restore_runtime_state()

def notify(title: str, msg: str, modal: bool = False):
    script = (f'display dialog "{msg}" with title "{title}" '
              f'buttons {{"忽略","关注"}} default button "关注"'
              if modal else
              f'display notification "{msg}" with title "{title}" sound name "Basso"')
    subprocess.Popen(['osascript', '-e', script])

def count_bucket_positions(name: str) -> int:
    with positions_lock:
        return sum(1 for v in positions.values() if v['bucket'] == name)


def in_reentry_cooldown(code: str, cooldown_minutes: int) -> bool:
    return broker.was_sold_recently(code, cooldown_minutes)


def mark_worker_beat(name: str, detail: str = ''):
    with _worker_lock:
        _worker_beats[name] = {
            'ts': time.time(),
            'detail': detail,
        }


def _fmt_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s前"
    if seconds < 3600:
        return f"{int(seconds // 60)}m前"
    return f"{seconds / 3600:.1f}h前"


def _fmt_uptime(seconds: float) -> str:
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _position_bucket_summary() -> str:
    state = broker.get_state()
    counts = {name: 0 for name in BUCKETS}
    counts['dca'] = 0
    counts['micro'] = 0
    for pos in state['positions'].values():
        bucket = str(pos.get('bucket', ''))
        counts[bucket] = counts.get(bucket, 0) + 1
    parts = []
    for key in ['conservative', 'longterm', 'shortterm', 'dca', 'micro']:
        val = counts.get(key, 0)
        if val:
            label = BUCKET_LABEL.get(key, key)
            parts.append(f"{label}{val}")
    return " / ".join(parts) if parts else "空仓"


def run_bot_heartbeat():
    print(f"[心跳] 运行监控启动，每 {HEARTBEAT_INTERVAL // 60} 分钟输出一次摘要")
    while True:
        state = broker.get_state()
        with _regime_lock:
            regime = _regime
        with _dynamic_lock:
            watch_count = len(_dynamic_watch)
        with _worker_lock:
            beats = dict(_worker_beats)

        worker_parts = []
        worker_order = ['基本面', '筛选', '底仓', '定投', 'Regime', '预警'] + [cfg['label'] for cfg in BUCKETS.values()]
        for worker in worker_order:
            beat = beats.get(worker)
            if not beat:
                continue
            age = _fmt_age(time.time() - float(beat['ts']))
            detail = str(beat.get('detail', '')).strip()
            worker_parts.append(f"{worker}:{age}" + (f"({detail})" if detail else ""))

        print(
            f"[心跳] {datetime.now().strftime('%H:%M:%S')} 运行中"
            f"  uptime={_fmt_uptime(time.time() - BOT_START_TS)}"
            f"  regime={regime}"
            f"  现金=${state['cash']:,.0f}"
            f"  持仓={len(state['positions'])} ({_position_bucket_summary()})"
            f"  watch={watch_count}"
        )
        if worker_parts:
            print("[心跳] 线程状态:", " | ".join(worker_parts))
        time.sleep(HEARTBEAT_INTERVAL)


def run_fundamental_refresh():
    initial_universe = current_watch_universe()
    print(f"[基本面] 慢速基本面刷新线程启动（日更缓存，观察池 {len(initial_universe)} 只）")
    mark_worker_beat('基本面', f"启动，观察池{len(initial_universe)}只")
    while True:
        try:
            universe = current_watch_universe()
            summary = refresh_fundamental_cache(universe)
            mark_worker_beat(
                '基本面',
                f"更新{summary['updated']} 跳过{summary['skipped']} 失败{summary['failed']} 扫描{len(universe)}",
            )
            print(
                f"[基本面] 日更完成：更新 {summary['updated']}，"
                f"跳过 {summary['skipped']}，失败 {summary['failed']}，"
                f"扫描 {len(universe)} 只"
            )
        except Exception as e:
            mark_worker_beat('基本面', f"异常:{e}")
            print(f"[基本面] 刷新异常：{e}")
        time.sleep(SLOW_FUND_REFRESH_INTERVAL)

# ══════════════════════════════════════════════════════════
# 动态筛选引擎 — 定期给宇宙全量评分，更新 watchlist
# ══════════════════════════════════════════════════════════
def _score_universe(ctx, universe: list[str]) -> dict[str, float]:
    """对当前观察池所有股票评分，返回 code→score 字典。"""
    scores: dict[str, float] = {}
    batch = 40
    for i in range(0, len(universe), batch):
        chunk = universe[i:i+batch]
        ret, snap = ctx.get_market_snapshot(chunk)
        if ret != RET_OK:
            continue
        snap = cast(pd.DataFrame, snap)
        for _, row in snap.iterrows():
            code = str(row['code'])
            try:
                scores[code] = score_snapshot_row(row)
            except Exception:
                pass
    return scores


def run_dynamic_screener():
    """持续运行的动态筛选线程，每隔 DYNAMIC_INTERVAL 秒更新 _dynamic_watch。"""
    ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
    initial_universe = current_watch_universe()
    print(f"[筛选] 动态筛选引擎启动，观察池 {len(initial_universe)} 只，每 {DYNAMIC_INTERVAL//60} 分钟更新")
    mark_worker_beat('筛选', f"启动，周期{DYNAMIC_INTERVAL//60}m")
    try:
        while True:
            universe = current_watch_universe()
            scores = _score_universe(ctx, universe)
            with _dynamic_lock:
                _dynamic_watch.clear()
                _dynamic_watch.update(scores)

            # 打印 top 15
            top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:15]
            print("[筛选] Top 15:", "  ".join(f"{c.replace('US.','')}({v})" for c, v in top))
            mark_worker_beat('筛选', f"已更新{len(scores)}只")
            time.sleep(DYNAMIC_INTERVAL)
    except Exception as e:
        mark_worker_beat('筛选', f"异常:{e}")
        print(f"[筛选] 异常：{e}")
    finally:
        ctx.close()


# ══════════════════════════════════════════════════════════
# 分散微量建仓 — 选 5-10 只跨板块股票买入 $MICRO_ALLOC 底仓
# ══════════════════════════════════════════════════════════

def run_micro_builder():
    """
    分散微量建仓线程。
    - 等待 _dynamic_watch 数据就绪
    - 目标持有 5-10 只跨板块底仓，默认向 8 只收敛
    - 单个板块默认只保留 1 只，避免底仓过度集中
    - 每只按 MICRO_ALLOC 固定现金建仓
    - 之后每 DYNAMIC_INTERVAL*2 秒重跑：补充缺失底仓，并对底仓执行止损
    """
    ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
    print(f"[底仓] 微量建仓线程启动（每只 ${MICRO_ALLOC:.0f}，目标 {MICRO_TARGET_POS} 只，"
          f"上限 {MICRO_MAX_POS} 只，单板块≤{MICRO_SECTOR_CAP}，最低评分 {MICRO_MIN_SCORE}）")
    mark_worker_beat('底仓', f"启动，目标{MICRO_TARGET_POS}只")
    try:
        # 等待筛选器首次完成
        for _ in range(30):
            with _dynamic_lock:
                ready = len(_dynamic_watch) > 0
            if ready:
                break
            time.sleep(10)

        while True:
            with _dynamic_lock:
                scores = dict(_dynamic_watch)

            state   = broker.get_state()
            cash    = state['cash']
            initial = state['initial_cash']
            held    = set(state['positions'].keys())
            micro_held = {
                code for code, pos in state['positions'].items()
                if pos.get('bucket') == 'micro'
            }
            micro_codes = [code for stocks in MICRO_SECTOR_UNIVERSE.values() for code in stocks]
            price_map: dict[str, float] = {}
            ret, snap = ctx.get_market_snapshot(micro_codes)
            if ret == RET_OK:
                snap = cast(pd.DataFrame, snap)
                price_map = {
                    str(row['code']): live_price_from_row(row)
                    for _, row in snap.iterrows()
                }
            target_new = max(0, min(MICRO_TARGET_POS, MICRO_MAX_POS) - len(micro_held))
            max_new = max(0, MICRO_MAX_POS - len(micro_held))
            if max_new <= 0:
                mark_worker_beat('底仓', f"已满{MICRO_MAX_POS}只")
                print(f"[底仓] 底仓已满（{MICRO_MAX_POS} 只），跳过本轮")
                time.sleep(DYNAMIC_INTERVAL * 2)
                continue

            # 价格过滤：有有效价格即可，不限制价格上限（高价股买 1 股也有价值）
            selection_universe = {
                sector: [
                    code for code in stocks
                    if price_map.get(code, 0.0) > 0
                ]
                for sector, stocks in MICRO_SECTOR_UNIVERSE.items()
            }
            raw_candidates = select_diversified_micro_candidates(
                scores,
                selection_universe,
                held,
                target_positions=target_new,
                max_positions=max_new,
                sector_cap=MICRO_SECTOR_CAP,
                min_score=MICRO_MIN_SCORE,
                sector_caps=MICRO_SECTOR_CAP_OVERRIDES,
                required_sectors=MICRO_REQUIRED_SECTORS,
            )
            candidates = [
                row for row in raw_candidates
                if not in_reentry_cooldown(row['code'], MICRO_REENTRY_COOLDOWN_MINUTES)
            ]
            if target_new <= 0:
                mark_worker_beat('底仓', f"达标{len(micro_held)}只")
                print(f"[底仓] 已达到默认目标（{MICRO_TARGET_POS} 只），本轮仅监控止损")
            elif not candidates:
                cooldown_hits = len(raw_candidates) - len(candidates)
                detail = f"缺口{target_new}，无候选"
                if cooldown_hits:
                    detail += f"，冷静期{cooldown_hits}"
                mark_worker_beat('底仓', detail)
                print(f"[底仓] 本轮无新增候选（缺口 {target_new}，评分阈值 {MICRO_MIN_SCORE}）")

            bought = 0
            for row in candidates:
                if bought >= target_new:
                    break
                code = row['code']
                score = row['score']
                sector = row['sector']
                budget = entry_budget(
                    cash, initial, 0.0, 'micro_position',
                    reserve_ratio=CASH_RESERVE,
                )
                if budget <= 0:
                    print(f"[底仓] 现金储备不足，停止建仓")
                    break

                price = price_map.get(code, 0.0)
                if price <= 0:
                    continue  # 批量快照无价格，跳过
                qty = cash_position_qty(price, budget)
                if qty <= 0:
                    continue
                cost = qty * price

                ok, msg = broker.place_order(
                    code, 'BUY', qty, price,
                    bucket='micro', reason='micro_position')
                if ok:
                    broker_pos = broker.get_state()['positions'].get(code)
                    with positions_lock:
                        positions[code] = runtime_position_snapshot(broker_pos or {})
                    cash -= cost
                    bought += 1
                    print(f"[底仓] ✅ {code:14s} {qty}股 @ ${price:.2f}"
                          f"  ≈${cost:.0f}  评分{score:.1f}  板块:{sector}  {msg}")

            if bought:
                mark_worker_beat('底仓', f"新建{bought}只，现有{len(state['positions'])}仓")
                print(f"[底仓] 本轮新建 {bought} 只底仓，剩余现金 ${cash:,.0f}")
            else:
                mark_worker_beat('底仓', f"候选{len(candidates)}只，无成交")
                print(f"[底仓] 本轮无新建仓（分散候选：{len(candidates)} 只）")

            # 底仓止损：跌超 10% 清出
            for code in list(micro_held):
                pos = state['positions'].get(code)
                if not pos:
                    continue
                price = price_map.get(code, 0.0)
                if price <= 0:
                    continue  # 批量快照无价格，跳过止损检查
                pnl_pct = (price - pos['avg_cost']) / pos['avg_cost']
                if pnl_pct <= -0.10:
                    ok, msg = broker.place_order(
                        code, 'SELL', pos['qty'], price,
                        bucket='micro', reason='micro_stop_loss')
                    if ok:
                        with positions_lock:
                            positions.pop(code, None)
                        print(f"[底仓] 🛑 止损清出 {code}  盈亏{pnl_pct*100:.1f}%  {msg}")

            time.sleep(DYNAMIC_INTERVAL * 2)
    except Exception as e:
        mark_worker_beat('底仓', f"异常:{e}")
        print(f"[底仓] 异常：{e}")
    finally:
        ctx.close()


def run_weekly_dca():
    if not WEEKLY_DCA_PLAN:
        print("[定投] 未配置定投标的，线程退出")
        return

    ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
    plan_desc = " / ".join(f"{code.replace('US.', '')}×{qty}" for code, qty in WEEKLY_DCA_PLAN.items())
    print(
        f"[定投] 每周定投线程启动（纽约时间每周"
        f"{['一','二','三','四','五','六','日'][WEEKLY_DCA_WEEKDAY_ET]} "
        f"{WEEKLY_DCA_MIN_HOUR_ET}:00 后执行：{plan_desc}）"
    )
    mark_worker_beat('定投', f"启动，周一 {WEEKLY_DCA_MIN_HOUR_ET}:00 ET")
    try:
        while True:
            now_et = current_new_york_time()
            marker = week_marker(now_et)

            if not should_execute_weekly_dca(
                now_et,
                '',
                WEEKLY_DCA_WEEKDAY_ET,
                WEEKLY_DCA_MIN_HOUR_ET,
            ):
                mark_worker_beat(
                    '定投',
                    f"等待 {now_et.strftime('%Y-%m-%d %H:%M')} ET",
                )
                time.sleep(WEEKLY_DCA_INTERVAL)
                continue

            ret, snap = ctx.get_market_snapshot(list(WEEKLY_DCA_PLAN.keys()))
            if ret != RET_OK:
                mark_worker_beat('定投', '快照失败')
                print("[定投] 快照获取失败，本轮跳过")
                time.sleep(WEEKLY_DCA_INTERVAL)
                continue

            snap = cast(pd.DataFrame, snap)
            price_map = {
                str(row['code']): live_price_from_row(row)
                for _, row in snap.iterrows()
            }

            buys = 0
            already_done = 0
            for code, qty in WEEKLY_DCA_PLAN.items():
                marker_key = f"weekly_dca:{code}"
                last_marker = broker.get_marker(marker_key, '')
                if not should_execute_weekly_dca(
                    now_et,
                    last_marker,
                    WEEKLY_DCA_WEEKDAY_ET,
                    WEEKLY_DCA_MIN_HOUR_ET,
                ):
                    already_done += 1
                    continue

                price = float(price_map.get(code, 0.0))
                if price <= 0:
                    print(f"[定投] {code} 实时价缺失，本轮跳过")
                    continue

                ok, msg = broker.place_order(
                    code, 'BUY', qty, price,
                    bucket='dca', reason='weekly_dca')
                if not ok:
                    print(f"[定投] {code} 定投失败: {msg}")
                    continue

                broker.set_marker(marker_key, marker)
                pos = broker.get_state()['positions'][code]
                with positions_lock:
                    positions[code] = runtime_position_snapshot(pos)
                buys += 1
                print(f"[定投] ✅ {code:12s} 每周定投 {qty}股 @ ${price:.2f}  {msg}")

            if buys:
                mark_worker_beat('定投', f"{marker} 已执行{buys}笔")
            elif already_done == len(WEEKLY_DCA_PLAN):
                mark_worker_beat('定投', f"{marker} 本周已完成")
            else:
                mark_worker_beat('定投', f"{marker} 无成交")

            time.sleep(WEEKLY_DCA_INTERVAL)
    except Exception as e:
        mark_worker_beat('定投', f"异常:{e}")
        print(f"[定投] 异常：{e}")
    finally:
        ctx.close()


# ══════════════════════════════════════════════════════════
# 市场状态监测 — SPY 200MA + VIX
# ══════════════════════════════════════════════════════════
def run_regime_monitor():
    """
    每小时检测一次市场状态：
      BULL   : SPY > 200MA  AND  VIX < 22  → 三桶全开
      NEUTRAL: 否则                         → 关闭短线桶
      BEAR   : SPY < 200MA×0.97 AND VIX > 28 → 只开保守桶
    """
    global _regime
    ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
    print("[Regime] 市场状态监测启动")
    mark_worker_beat('Regime', '启动')
    try:
        while True:
            try:
                ret_s, df_s, _ = ctx.request_history_kline(
                    'US.SPY', ktype=KLType.K_DAY, autype=AuType.QFQ, max_count=210)
                df_s = cast(pd.DataFrame, df_s)
                spy_200 = float(df_s['close'].rolling(200).mean().iloc[-1])
                spy_cur = float(df_s['close'].iloc[-1])

                ret_v, vix_snap = ctx.get_market_snapshot(['US.VIX'])
                vix_snap = cast(pd.DataFrame, vix_snap)
                vix = float(vix_snap.iloc[0]['last_price']) if ret_v == RET_OK else 20.0

                if spy_cur < spy_200 * 0.97 and vix > 28:
                    new_regime = 'BEAR'
                elif spy_cur >= spy_200 and vix < 22:
                    new_regime = 'BULL'
                else:
                    new_regime = 'NEUTRAL'

                with _regime_lock:
                    old = _regime
                    _regime = new_regime
                if old != new_regime:
                    notify(f"市场状态变化 → {new_regime}",
                           f"SPY={spy_cur:.0f} vs 200MA={spy_200:.0f}  VIX={vix:.1f}")
                mark_worker_beat('Regime', f"{new_regime} VIX={vix:.1f}")
                print(f"[Regime] {new_regime}  SPY={spy_cur:.0f}/{spy_200:.0f}"
                      f"  VIX={vix:.1f}")
            except Exception as e:
                mark_worker_beat('Regime', f"异常:{e}")
                print(f"[Regime] 检测异常：{e}")
            time.sleep(3600)
    finally:
        ctx.close()


# ══════════════════════════════════════════════════════════
# 暴跌预警
# ══════════════════════════════════════════════════════════
def run_crash_monitor():
    all_stocks = list({s for b in BUCKETS.values() for s in b['stocks']})
    ctx        = OpenQuoteContext(host='127.0.0.1', port=11111)
    alerted    = set()

    print(f"[预警] 启动，覆盖 {len(all_stocks)} 只股票")
    mark_worker_beat('预警', f"启动，覆盖{len(all_stocks)}只")
    try:
        while True:
            ret, snap = ctx.get_market_snapshot(all_stocks)
            if ret == RET_OK:
                snap = cast(pd.DataFrame, snap)
                for _, row in snap.iterrows():
                    code = str(row['code'])
                    last = float(row['last_price'])
                    prev = float(row['prev_close_price'])
                    if prev == 0:
                        continue
                    chg = (last - prev) / prev

                    if chg <= CRASH_THRESHOLD and code not in alerted:
                        rsi_val   = None
                        vol_note  = ''
                        df_day = request_kline(ctx, code, KLType.K_DAY, 30)
                        if df_day is not None:
                            rsi_val  = float(calc_rsi(df_day['close'], 14).iloc[-1])
                            _, vol_note = volume_signal(df_day)

                        fund_sc, fund_notes = fundamental_score(row)
                        rsi_str  = f"  RSI={rsi_val:.0f}" if rsi_val else ""
                        msg = (f"{code} 暴跌{chg*100:.1f}%  "
                               f"现价${last:.2f}{rsi_str}  "
                               f"基本面{fund_sc:.0f}/10({fund_notes[0]})  "
                               f"[{vol_note}]")

                        is_extreme = rsi_val is not None and rsi_val < CRASH_RSI_ALERT
                        if is_extreme:
                            print(f"[预警] 🚨 {msg}")
                            notify("🚨 极度超卖！潜在抄底机会", msg, modal=True)
                        else:
                            print(f"[预警] ⚠️  {msg}")
                            notify("⚠️ 暴跌预警", msg)
                        alerted.add(code)

                    elif chg > CRASH_THRESHOLD / 2 and code in alerted:
                        alerted.discard(code)
                mark_worker_beat('预警', f"扫描{len(all_stocks)}只，告警{len(alerted)}")
            else:
                mark_worker_beat('预警', '快照失败')
            time.sleep(300)
    finally:
        ctx.close()

# ══════════════════════════════════════════════════════════
# 单桶策略
# ══════════════════════════════════════════════════════════
def run_bucket(name: str, cfg: dict):
    label = cfg['label']
    while True:   # 外层：崩溃后自动重启
        _run_bucket_inner(name, cfg)
        mark_worker_beat(label, "异常退出，等待重启")
        print(f"[{label}] ⚠️ 线程异常退出，5秒后自动重启...")
        time.sleep(5)


def _run_bucket_inner(name: str, cfg: dict):
    min_bars = max(cfg['slow_ma'] + 5, 40)
    ctx      = OpenQuoteContext(host='127.0.0.1', port=11111)
    label    = cfg['label']
    print(f"[{label}] 启动  {len(cfg['stocks'])}只  最多{cfg['max_pos']}仓×{cfg['alloc']*100:.0f}%")
    mark_worker_beat(label, f"启动，周期{max(1, cfg['interval']//60)}m")

    try:
        while True:
            state        = broker.get_state()
            cash         = state['cash']
            initial_cash = state['initial_cash']

            # 每轮从 broker 重新同步内存持仓，防止多线程写入后脱节
            with positions_lock:
                for code, p in state['positions'].items():
                    if code not in positions:
                        positions[code] = runtime_position_snapshot(p)
                    else:
                        positions[code].update(runtime_position_snapshot(p))
                for code in list(positions.keys()):
                    if code not in state['positions']:
                        positions.pop(code, None)

            # 动态评分只用于排序；真实交易范围限制在本桶名单和当前持仓内。
            with _dynamic_lock:
                dyn = dict(_dynamic_watch)
            bucket_positions = [
                code for code, pos in state['positions'].items()
                if pos.get('bucket') == name
            ]
            base = list(dict.fromkeys(cfg['stocks'] + bucket_positions))
            stock_list = sorted(
                base,
                key=lambda code: (
                    0 if code in TRADE_UNIVERSE else 1,
                    -dyn.get(code, -999.0),
                    code,
                ),
            )

            # ── 市场状态过滤 ────────────────────────────────────
            with _regime_lock:
                regime = _regime
            if regime == 'BEAR' and name != 'conservative':
                mark_worker_beat(label, "BEAR暂停")
                print(f"[{label}] BEAR 市场，暂停操作（保守桶仍运行）")
                time.sleep(cfg['interval'])
                continue
            # 模拟仓模式：NEUTRAL 不再限制短线桶（仅 BEAR 时才全面收缩）

            # ── 批量获取本轮所有股票快照（避免循环内逐个查询触发频率限制）──
            snap_price_cache: dict = {}   # code→price 及 code+'_row'→Series
            batch_size = 20
            for i in range(0, len(stock_list), batch_size):
                chunk = stock_list[i:i + batch_size]
                ret_b, snap_b = ctx.get_market_snapshot(chunk)
                if ret_b == RET_OK:
                    snap_b = cast(pd.DataFrame, snap_b)
                    for _, r in snap_b.iterrows():
                        code = str(r['code'])
                        p = live_price_from_row(r)
                        if p > 0:
                            snap_price_cache[code] = p
                        snap_price_cache[code + '_row'] = r  # 缓存完整行供盘前异动检测

            for stock in stock_list:
                try:
                    df = request_kline(ctx, stock, cfg['ktype'], min_bars)
                    if df is None:
                        continue

                    df = enrich_indicators(df, cfg)

                    # ATR（按当前桶的 K 线周期计算，用于止损与仓位控制）
                    atr_val = calc_atr_value(df)

                    latest    = df.iloc[-1]
                    prev_row  = df.iloc[-2]

                    # 用批量快照缓存取实时价，避免逐个查询触发频率限制
                    live_price = snap_price_cache.get(stock, 0.0)
                    if live_price <= 0:
                        # 批量快照中仍无价格：持仓管理用 K 线估算，但不开新仓
                        kline_close = float(latest['close'])
                        print(f"[{label}] {stock} 实时价获取失败，用K线close={kline_close:.2f}（不开新仓）")
                        price = kline_close
                        _no_new_entry = True
                    else:
                        price = live_price
                        _no_new_entry = False
                    fast_now  = float(latest['fast_ma'])
                    slow_now  = float(latest['slow_ma_v'])
                    fast_prev = float(prev_row['fast_ma'])
                    slow_prev = float(prev_row['slow_ma_v'])

                    ma_golden = fast_prev < slow_prev and fast_now > slow_now
                    ma_death  = fast_prev > slow_prev and fast_now < slow_now

                    # ── 附加指标 ────────────────────────────
                    ind_state = indicator_state(name, cfg, latest, prev_row)
                    extra_buy = ind_state.extra_buy
                    extra_sell = ind_state.extra_sell
                    relaxed_buy = ind_state.relaxed_buy
                    ind_str = ind_state.ind_str
                    rsi_now = ind_state.rsi_now

                    entry_signal = detect_entry_signal(
                        cfg, df, latest, prev_row,
                        fast_now, slow_now, fast_prev, slow_prev,
                        extra_buy
                    )
                    add_signal = detect_entry_signal(
                        cfg, df, latest, prev_row,
                        fast_now, slow_now, fast_prev, slow_prev,
                        relaxed_buy
                    )

                    # ── 盘前异动 / 动量加速（补充信号）──────────
                    snap_row_cache = snap_price_cache.get(stock + '_row')
                    premarket_sig = None
                    momentum_sig  = None
                    if snap_row_cache is not None:
                        premarket_sig = detect_premarket_signal(
                            snap_row_cache,
                            min_gap_pct=2.0,
                            min_pre_vol=50_000,
                        )
                    momentum_sig = detect_momentum_surge(
                        df, fast_now, slow_now,
                        vol_surge_mult=2.0,
                        price_accel_pct=0.5,
                    )
                    # 优先级：entry_signal > momentum_surge > premarket_gap
                    if entry_signal is None:
                        entry_signal = momentum_sig or premarket_sig

                    # ── 持仓快照 ────────────────────────────
                    with positions_lock:
                        pos_data = dict(positions[stock]) if stock in positions else None
                    owns_position = pos_data is not None and pos_data.get('bucket') == name
                    held_by_other_bucket = pos_data is not None and pos_data.get('bucket') != name

                    # ── 持仓中：移动止损 / 分批出场 / 信号卖出 ─
                    if owns_position:
                        ep      = pos_data['entry_price']
                        qty     = pos_data['qty']
                        pnl_pct = (price - ep) / ep
                        held_days = holding_days(pos_data.get('entry_time'))

                        # 更新移动高水位（内存 + 持久化）
                        with _trail_lock:
                            new_high = max(_trailing_highs.get(stock, price), price)
                            if new_high != _trailing_highs.get(stock):
                                _trailing_highs[stock] = new_high
                                broker.update_trail_high(stock, new_high)
                            trail_high = _trailing_highs[stock]

                        trail_state = evaluate_trailing_stop(
                            ep,
                            price,
                            trail_high,
                            atr_val,
                            activate_profit=float(cfg.get('trail_activate_profit', 0.03)),
                            break_even_profit=float(cfg.get('break_even_profit', 0.05)),
                            break_even_buffer=float(cfg.get('break_even_buffer', 0.001)),
                            atr_mult=float(cfg.get('trail_atr_mult', 1.5)),
                        )
                        trail_stop = trail_state['effective_stop']

                        print(f"[{label}] {stock:12s} {price:>9.2f}"
                              f"  {ind_str}  盈亏:{pnl_pct*100:+.1f}%"
                              f"  持仓:{held_days}天"
                              f"  trail:{'on' if trail_state['trail_active'] else 'off'}"
                              f"  stop:${trail_stop:.2f}")

                        # ── 分批止盈（3 阶段）──────────────────────────
                        with _profit_lock:
                            stages_done = set(_profit_stages.get(stock, set()))

                        took_profit_this_cycle = False   # 防止止盈后立刻触发加仓

                        # 阶段 1：盈利 ≥ +8% → 卖出约 30%
                        if pnl_pct >= PROFIT_TAKE1_PCT and 1 not in stages_done and qty >= 3:
                            take_qty = max(1, round(qty * 0.30))
                            ok_h, msg_h = broker.place_order(
                                stock, 'SELL', take_qty, price,
                                bucket=name, reason='profit_take1')
                            if ok_h:
                                with _profit_lock:
                                    _profit_stages.setdefault(stock, set()).add(1)
                                    broker.update_profit_stages(stock, _profit_stages[stock])
                                with positions_lock:
                                    positions[stock]['qty'] = qty - take_qty
                                cash = broker.get_cash()
                                print(f"[{label}] 💰 止盈①  {stock}"
                                      f" -{take_qty}股（盈{pnl_pct*100:.1f}%，已锁定30%）  {msg_h}")
                                qty = qty - take_qty
                                stages_done.add(1)
                                took_profit_this_cycle = True

                        # 阶段 2：盈利 ≥ +15% → 再卖出约 60% 余仓（≈原仓位 40%）
                        if pnl_pct >= PROFIT_TAKE2_PCT and 2 not in stages_done and 1 in stages_done and qty >= 2:
                            take_qty = max(1, round(qty * 0.60))
                            ok_h, msg_h = broker.place_order(
                                stock, 'SELL', take_qty, price,
                                bucket=name, reason='profit_take2')
                            if ok_h:
                                with _profit_lock:
                                    _profit_stages.setdefault(stock, set()).add(2)
                                    broker.update_profit_stages(stock, _profit_stages[stock])
                                with positions_lock:
                                    positions[stock]['qty'] = qty - take_qty
                                cash = broker.get_cash()
                                print(f"[{label}] 💰 止盈②  {stock}"
                                      f" -{take_qty}股（盈{pnl_pct*100:.1f}%，余仓移动止损）  {msg_h}")
                                qty = qty - take_qty
                                took_profit_this_cycle = True

                        sell_reason = None
                        if pnl_pct <= -cfg['stop_loss']:
                            sell_reason = 'stop_loss'
                        elif trail_state['triggered']:
                            sell_reason = 'trailing_stop'
                        elif should_time_stop(
                            pos_data.get('entry_time'),
                            pnl_pct,
                            max_days=int(cfg.get('time_stop_days', 0)),
                            min_return=float(cfg.get('time_stop_min_return', 0.0)),
                        ):
                            sell_reason = 'time_stop'
                        elif ma_death and (name != 'longterm' or extra_sell):
                            sell_reason = 'death_cross'
                        elif extra_sell and name == 'conservative':
                            sell_reason = 'rsi_overbought'

                        if sell_reason:
                            ok, msg = broker.place_order(
                                stock, 'SELL', qty, price,
                                bucket=name, reason=sell_reason)
                            if ok:
                                tag = {'stop_loss':'🛑 止损',
                                       'trailing_stop':'📉 移动止损',
                                       'time_stop':'⏳ 时间止损',
                                       'death_cross':'🔴 死叉',
                                       'rsi_overbought':'🟡 RSI超买'}.get(sell_reason,'卖出')
                                print(f"[{label}] {tag} {stock}  {msg}")
                                with positions_lock:
                                    positions.pop(stock, None)
                                with _trail_lock:
                                    _trailing_highs.pop(stock, None)
                                with _profit_lock:
                                    _profit_stages.pop(stock, None)
                                cash = broker.get_cash()

                        else:
                            # 本轮触发止盈 → 跳过所有加仓，防止卖完立刻买回
                            if took_profit_this_cycle:
                                pass

                            else:
                                # ── starter 仓位：趋势确认后自动晋级 ──────
                                _bp = broker.get_state()['positions'].get(stock, {})
                                _add_count = int(_bp.get('add_count', 0))
                                current_value = qty * price
                                starter_like = is_starter_position(
                                    current_value, initial_cash, cfg['alloc'],
                                    threshold=float(cfg.get('starter_ratio', 0.35)),
                                )

                                if (_add_count < 1
                                        and starter_like
                                        and add_signal is not None):
                                    signal_reason, signal_note = add_signal
                                    budget = entry_budget(
                                        cash, initial_cash, cfg['alloc'],
                                        'starter_promotion',
                                        reserve_ratio=CASH_RESERVE,
                                        current_position_value=current_value,
                                    )
                                    add_qty = atr_position_qty(
                                        price, atr_val, budget,
                                        reason='starter_promotion',
                                    )
                                    if add_qty > 0:
                                        ok2, msg2 = broker.place_order(
                                            stock, 'BUY', add_qty, price,
                                            bucket=name, reason=signal_reason)
                                        if ok2:
                                            print(f"[{label}] ⬆️ starter晋级 {stock}"
                                                  f" +{add_qty}股 @ ${price:.2f}"
                                                  f"  事件:{ENTRY_REASON_LABEL.get(signal_reason, signal_reason)}"
                                                  f" | {signal_note}  {msg2}")
                                            broker_pos = broker.get_state()['positions'][stock]
                                            with positions_lock:
                                                positions[stock] = runtime_position_snapshot(broker_pos)
                                            cash = broker.get_cash()
                                            continue

                                # ── 分批建仓（Pyramid）：最多加仓两次 ──────
                                _entry = _bp.get('entry_time', '')
                                _days  = holding_days(_entry)
                                _rsi_ok = (rsi_now < cfg.get('add_rsi_max', ADD_RSI_MAX)
                                           if name == 'conservative' else True)
                                _trend  = fast_now > slow_now

                                # 第二批：盈利 ≥ +5%，持仓 ≥ 7 天
                                if (_add_count < 1
                                        and pnl_pct >= PYRAMID_ADD1_PROFIT
                                        and _days >= PYRAMID_ADD1_DAYS
                                        and _trend and _rsi_ok):
                                    budget = entry_budget(
                                        cash, initial_cash, cfg['alloc'],
                                        'pyramid_stage2',
                                        reserve_ratio=CASH_RESERVE,
                                    )
                                    _add_qty = atr_position_qty(
                                        price, atr_val, budget,
                                        reason='pyramid_stage2',
                                    )
                                    if _add_qty > 0:
                                        ok2, msg2 = broker.place_order(
                                            stock, 'BUY', _add_qty, price,
                                            bucket=name, reason='pyramid_stage2')
                                        if ok2:
                                            print(f"[{label}] ➕ 金字塔② {stock}"
                                                  f" +{_add_qty}股 @ ${price:.2f}"
                                                  f"（盈{pnl_pct*100:.1f}%，持{_days}天）  {msg2}")
                                            broker_pos = broker.get_state()['positions'][stock]
                                            with positions_lock:
                                                positions[stock] = runtime_position_snapshot(broker_pos)
                                            cash = broker.get_cash()

                                # 第三批：盈利 ≥ +12%，持仓 ≥ 14 天
                                elif (_add_count == 1
                                        and pnl_pct >= PYRAMID_ADD2_PROFIT
                                        and _days >= PYRAMID_ADD2_DAYS
                                        and _trend):
                                    budget = entry_budget(
                                        cash, initial_cash, cfg['alloc'],
                                        'pyramid_stage3',
                                        reserve_ratio=CASH_RESERVE,
                                    )
                                    _add_qty = atr_position_qty(
                                        price, atr_val, budget,
                                        reason='pyramid_stage3',
                                    )
                                    if _add_qty > 0:
                                        ok2, msg2 = broker.place_order(
                                            stock, 'BUY', _add_qty, price,
                                            bucket=name, reason='pyramid_stage3')
                                        if ok2:
                                            print(f"[{label}] ➕ 金字塔③ {stock}"
                                                  f" +{_add_qty}股 @ ${price:.2f}"
                                                  f"（盈{pnl_pct*100:.1f}%，持{_days}天）  {msg2}")
                                            broker_pos = broker.get_state()['positions'][stock]
                                            with positions_lock:
                                                positions[stock] = runtime_position_snapshot(broker_pos)
                                            cash = broker.get_cash()

                    # ── 空仓：三层分级买入逻辑 ────────────────────────
                    else:
                        if held_by_other_bucket:
                            continue

                        # 实时价获取失败时禁止开新仓，避免用历史K线价买入
                        if _no_new_entry:
                            continue

                        if in_reentry_cooldown(stock, REENTRY_COOLDOWN_MINUTES):
                            reason = broker.last_sell_reason(stock)
                            reason_note = f" 上次卖出:{reason}" if reason else ''
                            print(f"[{label}] {stock} 卖出后冷静期内，跳过{reason_note}")
                            continue

                        sector = SECTOR_MAP.get(stock, '')
                        ret_snap, snap_df = ctx.get_market_snapshot([stock])
                        snap_df = cast(pd.DataFrame, snap_df)
                        snap_row = snap_df.iloc[0] if ret_snap == RET_OK and len(snap_df) else None

                        # 1. 快速快照评分 + 量价过滤
                        if ret_snap == RET_OK:
                            fund_sc, fund_notes = fundamental_score(snap_row)
                            vol_sig, vol_note   = volume_signal(df)
                        else:
                            fund_sc, fund_notes = 5.0, ['快照缺失']
                            vol_sig, vol_note   = 'neutral', ''

                        if vol_sig == 'negative':
                            print(f"[{label}] {stock} 量价信号负面({vol_note})，观望")
                            continue

                        # 2. 日更慢速基本面优先；缺失时退回快照评分
                        slow_entry = get_fundamental_entry(stock, refresh_if_stale=False)
                        slow_eval = score_slow_fundamentals(
                            stock,
                            sector,
                            slow_entry,
                            snapshot=snap_row,
                        )
                        slow_sc = slow_eval['score']
                        slow_notes = slow_eval['notes']

                        if slow_eval['available']:
                            if slow_sc < SLOW_FUND_MIN_SCORE[name]:
                                print(f"[{label}] {stock} 慢基本面不达标"
                                      f"({slow_sc:.0f}/100)，跳过")
                                continue
                            fund_gate_value = slow_sc
                            fund_gate_label = f"慢分{slow_sc:.0f}/100"
                            fund_note = slow_notes[0] if slow_notes else '慢分通过'
                        else:
                            if fund_sc < MIN_FUND_SCORE[name]:
                                print(f"[{label}] {stock} 快照基本面不达标"
                                      f"({fund_sc:.0f}/10)，跳过")
                                continue
                            fund_gate_value = fund_sc * 10
                            fund_gate_label = f"快分{fund_sc:.0f}/10"
                            fund_note = fund_notes[0] if fund_notes else '快分通过'

                        # 3. 三层分级：慢分决定质量层，快分只做兜底
                        if fund_gate_value >= SLOW_FUND_TIER_FULL:
                            # 一层：蓝筹底仓 ── 趋势确认即建仓，满仓
                            chosen_signal = (
                                detect_uptrend(df, fast_now, slow_now)
                                or entry_signal
                            )
                            alloc_mult = 1.0
                            tier_label = f"高质量({fund_gate_label})"
                        elif fund_gate_value >= SLOW_FUND_TIER_MID:
                            # 二层：成长趋势 ── 普通信号，七成仓
                            chosen_signal = entry_signal
                            if cfg.get('prefer_uptrend_entry'):
                                chosen_signal = detect_uptrend(df, fast_now, slow_now) or chosen_signal
                            alloc_mult = 0.7
                            tier_label = f"均衡质量({fund_gate_label})"
                        else:
                            # 三层：热门赛道 ── 普通信号，四成小仓
                            chosen_signal = entry_signal
                            alloc_mult = 0.4
                            tier_label = f"试探仓({fund_gate_label})"

                        if chosen_signal is None:
                            continue

                        signal_reason, signal_note = chosen_signal

                        if count_bucket_positions(name) < cfg['max_pos']:
                            min_cash = initial_cash * CASH_RESERVE
                            if cash <= min_cash:
                                print(f"[{label}] {stock} 现金储备不足，跳过")
                                continue
                            alloc_cash = entry_budget(
                                cash, initial_cash, cfg['alloc'],
                                signal_reason,
                                reserve_ratio=CASH_RESERVE,
                            ) * alloc_mult
                            qty = atr_position_qty(
                                price, atr_val, alloc_cash,
                                reason=signal_reason,
                            )
                            if qty <= 0:
                                print(f"[{label}] {stock} 预算不足，跳过")
                                continue
                            ok, msg = broker.place_order(
                                stock, 'BUY', qty, price,
                                bucket=name, reason=signal_reason)
                            if ok:
                                screen = (f"{tier_label}"
                                          f" | {ENTRY_REASON_LABEL.get(signal_reason, signal_reason)}"
                                          f"({signal_note})"
                                          f" | {fund_gate_label}({fund_note})"
                                          f" | ATR={atr_val:.2f}")
                                print(f"[{label}] ✅ {stock} {msg}"
                                      f"  {ind_str} | {screen}")
                                broker_pos = broker.get_state()['positions'].get(stock)
                                with positions_lock:
                                    positions[stock] = runtime_position_snapshot(broker_pos or {})
                                with _trail_lock:
                                    _trailing_highs[stock] = price
                                with _profit_lock:
                                    _profit_stages.pop(stock, None)
                                cash = broker.get_cash()
                        else:
                            print(f"[{label}] {stock} 已满{cfg['max_pos']}仓，跳过")

                except Exception as e:
                    print(f"[{label}] {stock} 异常：{e}")

            mark_worker_beat(label, f"完成{len(stock_list)}只扫描")
            time.sleep(cfg['interval'])

    except Exception as e:
        mark_worker_beat(label, f"崩溃:{e}")
        print(f"[{label}] 崩溃：{e}")
    finally:
        ctx.close()

# ══════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════
threads = [
    threading.Thread(target=run_bot_heartbeat, daemon=True, name='heartbeat'),
    threading.Thread(target=run_fundamental_refresh, daemon=True, name='fundamental_refresh'),
    threading.Thread(target=run_regime_monitor,  daemon=True, name='regime'),
    threading.Thread(target=run_dynamic_screener, daemon=True, name='screener'),
    threading.Thread(target=run_micro_builder,    daemon=True, name='micro'),
    threading.Thread(target=run_weekly_dca,      daemon=True, name='weekly_dca'),
] + [
    threading.Thread(target=run_bucket, args=(name, cfg), daemon=True, name=f'bucket_{name}')
    for name, cfg in BUCKETS.items()
] + [
    threading.Thread(target=run_crash_monitor, daemon=True, name='crash_monitor')
]

for t in threads:
    t.start()

print("┌──────────────────────────────────────────────────┐")
print("│  Portfolio Bot（本地撮合模式）已启动              │")
print("│  行情：moomoo 真实数据  交易：本地虚拟执行        │")
print("│  金叉 / 回踩 / 突破  + 金字塔建仓 + 分批止盈        │")
print("│  Ctrl+C 停止                                     │")
print("└──────────────────────────────────────────────────┘")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n所有策略已停止")
