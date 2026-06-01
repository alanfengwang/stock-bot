# Moomoo 量化交易 Bot — 项目交接文档
> 供 VSCode Claude（CC）接管后续开发使用

---

## 1. 项目概览

**目标**：在富途（Moomoo）模拟仓上运行基于双均线的自动交易策略，逐步扩展为多策略、多标的的量化交易系统。

**当前状态**：✅ 基础框架已跑通，策略正在运行中。

---

## 2. 环境信息

| 项目 | 详情 |
|------|------|
| 操作系统 | macOS |
| Python版本 | 3.14（路径：`/usr/local/bin/python3.14`） |
| 包管理 | pip（用 `python3.14 -m pip install` 安装） |
| 项目目录 | `~/Documents/Stock/` |
| 已安装依赖 | `moomoo-api==10.6.6608`, `numpy`, `pandas`, `PyCryptodome`, `protobuf`, `simplejson` |

---

## 3. 架构说明

```
用户本地
├── OpenD（富途网关，必须保持运行）
│   └── 监听 127.0.0.1:11111
│
└── Python策略脚本
    ├── OpenQuoteContext  →  行情数据（K线、快照）
    └── OpenSecTradeContext  →  交易执行（下单、查持仓）
```

**关键规则**：
- `OpenSecTradeContext` 初始化时**不传** `trd_env`
- `trd_env=TrdEnv.SIMULATE` 在每个交易函数调用时单独传入
- 行情函数：`request_history_kline`（不是 `get_history_kline`，新版已改名）

---

## 4. 已完成文件

### `test_simulate.py` — 模拟仓连接测试
```python
from moomoo import *

trd_ctx = OpenSecTradeContext(host='127.0.0.1', port=11111)
ret, data = trd_ctx.accinfo_query(trd_env=TrdEnv.SIMULATE)

if ret == RET_OK:
    print("模拟仓账户信息：")
    print(data[['cash', 'total_assets', 'market_val']])
else:
    print("错误：", data)

trd_ctx.close()
```
**运行结果**：模拟仓初始资金 $1,000,000，连接正常。

---

### `ma_strategy.py` — 双均线自动交易策略（当前运行中）
```python
from moomoo import *
import time

# ============ 策略参数 ============
STOCK = 'US.AAPL'
FAST_MA = 5
SLOW_MA = 20
QTY = 10
CHECK_INTERVAL = 60
# ==================================

quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
trd_ctx = OpenSecTradeContext(host='127.0.0.1', port=11111)
position = 0

try:
    while True:
        ret, df, _ = quote_ctx.request_history_kline(
            STOCK,
            ktype=KLType.K_DAY,
            autype=AuType.QFQ,
            max_count=SLOW_MA + 5
        )

        if ret != RET_OK:
            time.sleep(CHECK_INTERVAL)
            continue

        df['fast_ma'] = df['close'].rolling(FAST_MA).mean()
        df['slow_ma'] = df['close'].rolling(SLOW_MA).mean()

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        fast_now, slow_now = latest['fast_ma'], latest['slow_ma']
        fast_prev, slow_prev = prev['fast_ma'], prev['slow_ma']
        price = latest['close']

        print(f"[{latest['time_key']}] 价格:{price:.2f} 快线:{fast_now:.2f} 慢线:{slow_now:.2f}")

        # 金叉 → 买入
        if fast_prev < slow_prev and fast_now > slow_now and position == 0:
            ret, data = trd_ctx.place_order(
                price=price, qty=QTY, code=STOCK,
                trd_side=TrdSide.BUY,
                order_type=OrderType.NORMAL,
                trd_env=TrdEnv.SIMULATE
            )
            if ret == RET_OK:
                print(f"✅ 买入 {QTY}股 @ {price}")
                position = 1

        # 死叉 → 卖出
        elif fast_prev > slow_prev and fast_now < slow_now and position == 1:
            ret, data = trd_ctx.place_order(
                price=price, qty=QTY, code=STOCK,
                trd_side=TrdSide.SELL,
                order_type=OrderType.NORMAL,
                trd_env=TrdEnv.SIMULATE
            )
            if ret == RET_OK:
                print(f"🔴 卖出 {QTY}股 @ {price}")
                position = 0

        time.sleep(CHECK_INTERVAL)

except KeyboardInterrupt:
    quote_ctx.close()
    trd_ctx.close()
```

**当前运行状态**：
- AAPL 价格 $209.18，快线 $209.92，慢线 $201.93
- 快线在慢线上方，趋势向上，等待新的金叉/死叉信号

---

## 5. 待开发任务（按优先级）

### P1 — 立即可做

**5.1 加入止损逻辑**
```python
# 在策略循环里加入：
STOP_LOSS = 0.05  # 5%止损

if position == 1:
    if price < entry_price * (1 - STOP_LOSS):
        # 触发止损，强制卖出
```

**5.2 记录交易日志**
```python
import csv
# 每次下单后写入 trade_log.csv
# 字段：时间、股票、方向、价格、数量、原因
```

**5.3 换成目标标的**
当前用AAPL测试，实际关注的标的：
- `US.KTOS`（Kratos Defense，无人机）
- `US.MRVL`（Marvell，AI光互联芯片）
- `US.VST`（Vistra，AI电力）
- `US.FOTO`（光子产业链ETF，注意流动性极差）

---

### P2 — 进阶功能

**5.4 多标的同时监控**
```python
STOCKS = ['US.KTOS', 'US.MRVL', 'US.VST']
# 用多线程或异步同时跑多个策略
```

**5.5 回测模块**
用历史K线数据验证策略效果，输出：
- 总收益率
- 夏普比率
- 最大回撤
- 胜率

**5.6 仓位管理**
```python
# 单笔最大损失不超过总资金2%
# 单只股票最大仓位不超过20%
```

---

### P3 — 长期优化

**5.7 事件驱动策略**（结合今天的供应链分析逻辑）
- 财报超预期 + 板块催化剂 → 触发买入
- 内部人大量减持 → 触发卖出警告

**5.8 Web监控面板**
用Flask或Streamlit做一个简单的本地网页，实时显示：
- 当前持仓盈亏
- 策略信号状态
- 交易历史

---

## 6. 已知的API坑（避免重复踩）

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| `filter_trd_market` 参数报错 | 新版API删除了这个初始化参数 | 初始化时不传，在查询函数里传 |
| `trd_env` 参数报错 | 同上 | 在 `place_order()`、`accinfo_query()` 等函数里传 |
| `get_history_kline` 不存在 | 新版改名 | 用 `request_history_kline` |
| VS Code运行Python版本不对 | 系统有多个Python | 始终用 `/usr/local/bin/python3.14` 运行 |
| 模块找不到 | pip装到了其他Python版本 | 用 `python3.14 -m pip install` 安装 |

---

## 7. 运行方式

```bash
# 前置条件：确保OpenD已启动并登录

# 测试模拟仓连接
/usr/local/bin/python3.14 ~/Documents/Stock/test_simulate.py

# 运行双均线策略
/usr/local/bin/python3.14 ~/Documents/Stock/ma_strategy.py

# 停止策略
Ctrl+C
```

---

## 8. 参考资源

- 富途API官方文档：https://openapi.moomoo.com/moomoo-api-doc/en/
- 交易函数参考：https://openapi.moomoo.com/moomoo-api-doc/en/trade/
- 行情函数参考：https://openapi.moomoo.com/moomoo-api-doc/en/quote/
- moomoo-api GitHub：https://github.com/futu-inc/moomoo-api-sdk
