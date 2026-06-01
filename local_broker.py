"""
local_broker.py — 本地虚拟撮合引擎

以真实 moomoo 行情价格为基准，在本地模拟买卖、持仓、盈亏。
不调用任何交易 API，完全无风险。模拟万三手续费（最低 $1）。
"""
from __future__ import annotations
import json, os, csv, threading
from datetime import datetime

from trade_costs import calc_commission


class LocalBroker:
    def __init__(self, db_path: str, log_path: str,
                 initial_cash: float = 1_000_000.0):
        self.db_path  = db_path
        self.log_path = log_path
        self._lock    = threading.Lock()

        if not os.path.exists(db_path):
            self._write({
                'initial_cash':     initial_cash,
                'cash':             initial_cash,
                'realized_pnl':     0.0,
                'total_commission': 0.0,
                'positions': {},
                # positions[code] = {qty, avg_cost, bucket, entry_time}
            })

    # ── 内部 IO ────────────────────────────────────────────
    def _read(self) -> dict:
        with open(self.db_path) as f:
            return json.load(f)

    def _write(self, state: dict):
        with open(self.db_path, 'w') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

    def _log(self, time_key, code, bucket, side, price, qty, reason, pnl=None):
        exists = os.path.exists(self.log_path)
        with open(self.log_path, 'a', newline='') as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(['time','stock','bucket','side','price','qty','reason','pnl'])
            w.writerow([time_key, code, bucket, side,
                        f'{price:.4f}', qty, reason,
                        f'{pnl:.2f}' if pnl is not None else ''])

    # ── 下单 ───────────────────────────────────────────────
    def place_order(self, code: str, side: str, qty: int, price: float,
                    bucket: str = '', reason: str = '') -> tuple[bool, str]:
        """
        side: 'BUY' | 'SELL'
        Returns (success, message)
        """
        if qty <= 0 or price <= 0:
            return False, f"无效参数 qty={qty} price={price}"

        commission = calc_commission(price, qty)
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with self._lock:
            s = self._read()

            if side == 'BUY':
                total_cost = price * qty + commission
                if s['cash'] < total_cost:
                    return False, (f"现金不足 ${s['cash']:,.0f}，"
                                   f"需要 ${total_cost:,.0f}")

                s['cash']            -= total_cost
                s['total_commission'] += commission

                pos = s['positions']
                if code in pos:
                    old = pos[code]
                    new_qty  = old['qty'] + qty
                    avg_cost = (old['qty'] * old['avg_cost'] + qty * price) / new_qty
                    old['qty']       = new_qty
                    old['avg_cost']  = round(avg_cost, 6)
                    old['add_count'] = old.get('add_count', 0) + 1
                else:
                    pos[code] = {
                        'qty':          qty,
                        'avg_cost':     price,
                        'bucket':       bucket,
                        'entry_time':   now,
                        'add_count':    0,
                        'profit_stages': [],      # 已触发止盈阶段 [1, 2]
                        'trail_high':   price,    # 移动止损高水位
                    }

                self._write(s)
                self._log(now, code, bucket, 'BUY', price, qty, reason)
                return True, (f"✅ 买入 {qty}股 @ ${price:.2f}"
                              f"  手续费 ${commission:.2f}")

            elif side == 'SELL':
                pos = s['positions']
                if code not in pos:
                    return False, f"无 {code} 持仓"

                p        = pos[code]
                sell_qty = min(qty, p['qty'])
                pnl      = (price - p['avg_cost']) * sell_qty - commission

                s['cash']            += price * sell_qty - commission
                s['realized_pnl']    += pnl
                s['total_commission'] += commission

                if sell_qty >= p['qty']:
                    del pos[code]
                else:
                    p['qty'] -= sell_qty

                self._write(s)
                self._log(now, code, p.get('bucket', bucket), 'SELL',
                          price, sell_qty, reason, pnl)
                return True, (f"卖出 {sell_qty}股 @ ${price:.2f}"
                              f"  盈亏 ${pnl:+.2f}  手续费 ${commission:.2f}")

        return False, "未知错误"

    # ── 查询 ───────────────────────────────────────────────
    def get_state(self) -> dict:
        with self._lock:
            return self._read()

    def get_cash(self) -> float:
        return self.get_state()['cash']

    def update_trail_high(self, code: str, high: float):
        """更新某只股票的移动止损高水位（持久化）。"""
        with self._lock:
            s = self._read()
            if code in s['positions']:
                s['positions'][code]['trail_high'] = round(high, 4)
                self._write(s)

    def update_profit_stages(self, code: str, stages: set):
        """更新某只股票的已触发止盈阶段（持久化）。"""
        with self._lock:
            s = self._read()
            if code in s['positions']:
                s['positions'][code]['profit_stages'] = sorted(stages)
                self._write(s)

    def get_trail_high(self, code: str, default: float = 0.0) -> float:
        s = self.get_state()
        return float(s['positions'].get(code, {}).get('trail_high', default))

    def get_profit_stages(self, code: str) -> set:
        s = self.get_state()
        return set(s['positions'].get(code, {}).get('profit_stages', []))

    def total_assets(self, price_map: dict[str, float]) -> float:
        """传入 {code: last_price} 计算总资产。"""
        s = self.get_state()
        mkt_val = sum(
            p['qty'] * price_map.get(code, p['avg_cost'])
            for code, p in s['positions'].items()
        )
        return s['cash'] + mkt_val
