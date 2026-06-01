from moomoo import *
import time
import csv
import os
import threading
import pandas as pd
from typing import cast

# ============ 策略参数 ============
STOCKS = ['US.KTOS', 'US.MRVL', 'US.VST']  # 监控标的列表
FAST_MA = 5
SLOW_MA = 20
QTY = 10
CHECK_INTERVAL = 300     # 秒（5分钟K线，每根收完查一次）
STOP_LOSS = 0.05
KTYPE = KLType.K_5M
LOG_FILE = os.path.join(os.path.dirname(__file__), 'trade_log.csv')
# =================================

log_lock   = threading.Lock()  # 保护 CSV 写入
trade_lock = threading.Lock()  # 保护下单（共享 trd_ctx）

def log_trade(time_key, stock, side, price, qty, reason):
    with log_lock:
        file_exists = os.path.exists(LOG_FILE)
        with open(LOG_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['time', 'stock', 'side', 'price', 'qty', 'reason'])
            writer.writerow([time_key, stock, side, f'{price:.2f}', qty, reason])

def run_strategy(stock, trd_ctx):
    # 每个线程独立一条行情连接，避免并发冲突
    quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
    position  = 0
    entry_price = 0.0

    print(f"[{stock}] 启动，MA{FAST_MA}/MA{SLOW_MA}，止损 {STOP_LOSS*100:.0f}%，{KTYPE}")

    try:
        while True:
            ret, df, _ = quote_ctx.request_history_kline(
                stock,
                ktype=KTYPE,
                autype=AuType.QFQ,
                max_count=SLOW_MA + 5
            )
            df = cast(pd.DataFrame, df)

            if ret != RET_OK:
                print(f"[{stock}] K线失败：{df}")
                time.sleep(CHECK_INTERVAL)
                continue

            df['fast_ma'] = df['close'].rolling(FAST_MA).mean()
            df['slow_ma'] = df['close'].rolling(SLOW_MA).mean()

            latest    = df.iloc[-1]
            prev      = df.iloc[-2]
            fast_now  = latest['fast_ma']
            slow_now  = latest['slow_ma']
            fast_prev = prev['fast_ma']
            slow_prev = prev['slow_ma']
            price     = latest['close']
            time_key  = latest['time_key']

            status = f"[{stock}] [{time_key}] 价格:{price:.2f} 快线:{fast_now:.2f} 慢线:{slow_now:.2f}"
            if position == 1:
                pnl_pct = (price - entry_price) / entry_price * 100
                status += f" | 持仓盈亏:{pnl_pct:+.2f}%"
            print(status)

            # 止损（最高优先级）
            if position == 1 and price < entry_price * (1 - STOP_LOSS):
                with trade_lock:
                    ret, data = trd_ctx.place_order(
                        price=price, qty=QTY, code=stock,
                        trd_side=TrdSide.SELL,
                        order_type=OrderType.NORMAL,
                        trd_env=TrdEnv.SIMULATE
                    )
                if ret == RET_OK:
                    loss_pct = (price - entry_price) / entry_price * 100
                    print(f"[{stock}] 🛑 止损！卖出 {QTY} 股 @ {price}（亏损 {loss_pct:.2f}%）")
                    log_trade(time_key, stock, 'SELL', price, QTY, 'stop_loss')
                    position = 0
                    entry_price = 0.0
                else:
                    print(f"[{stock}] 止损下单失败：{data}")

            # 金叉买入
            elif fast_prev < slow_prev and fast_now > slow_now and position == 0:
                with trade_lock:
                    ret, data = trd_ctx.place_order(
                        price=price, qty=QTY, code=stock,
                        trd_side=TrdSide.BUY,
                        order_type=OrderType.NORMAL,
                        trd_env=TrdEnv.SIMULATE
                    )
                if ret == RET_OK:
                    print(f"[{stock}] ✅ 金叉买入 {QTY} 股 @ {price}")
                    log_trade(time_key, stock, 'BUY', price, QTY, 'golden_cross')
                    position = 1
                    entry_price = price
                else:
                    print(f"[{stock}] 下单失败：{data}")

            # 死叉卖出
            elif fast_prev > slow_prev and fast_now < slow_now and position == 1:
                with trade_lock:
                    ret, data = trd_ctx.place_order(
                        price=price, qty=QTY, code=stock,
                        trd_side=TrdSide.SELL,
                        order_type=OrderType.NORMAL,
                        trd_env=TrdEnv.SIMULATE
                    )
                if ret == RET_OK:
                    pnl = (price - entry_price) * QTY
                    print(f"[{stock}] 🔴 死叉卖出 {QTY} 股 @ {price}（盈亏: {pnl:+.2f}）")
                    log_trade(time_key, stock, 'SELL', price, QTY, 'death_cross')
                    position = 0
                    entry_price = 0.0
                else:
                    print(f"[{stock}] 下单失败：{data}")

            else:
                print(f"[{stock}] 无信号")

            time.sleep(CHECK_INTERVAL)

    except Exception as e:
        print(f"[{stock}] 线程异常退出：{e}")
    finally:
        quote_ctx.close()

# 共享一条交易连接
trd_ctx = OpenSecTradeContext(host='127.0.0.1', port=11111)

threads = [
    threading.Thread(target=run_strategy, args=(stock, trd_ctx), daemon=True)
    for stock in STOCKS
]
for t in threads:
    t.start()

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n所有策略已停止")
    trd_ctx.close()
