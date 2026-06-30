# Stock Research Bot

用 Python 写的美股量化交易模拟系统，$1M 虚拟资金，三桶策略（保守/成长/短线），自动选股、买卖、止损止盈。

不做真实下单，所有交易记录在本地 `virtual_account.json`。

## 回测结果（2025 上半年）

用 TradingView 历史数据回测 2025-01-01 到 2025-06-30：

| 指标 | 结果 |
|------|------|
| 初始资金 | $1,000,000 |
| 总盈亏 | **+$195,806** |
| 收益率 | **+19.6%** |
| 胜率 | **73%** |
| 交易笔数 | 364 |

保守桶 +$26,461（年化 5.4%），成长桶 +$102,745（年化 24.7%），短线桶 +$66,601（年化 18.7%）

## 策略逻辑

**三桶结构：**
- 保守桶：AAPL/MSFT/GOOGL 等蓝筹，10MA/50MA 金叉，RSI 过滤
- 成长桶：NVDA/AMD/TSM/STX 等半导体，5MA/20MA 金叉，MACD 确认
- 短线桶：TSLA/PLTR/OKLO 等高波动，量比确认

**买入条件：**
- 金叉（快线上穿慢线）+ ADX ≥ 18（趋势强度确认，过滤震荡假信号）
- 趋势中回踩均线后重新站上
- 放量突破近期高点
- 盈利 +5%/+12% 后金字塔加仓

**仓位管理：**
- 每笔使用该股票分配额的 55-70%（Vibe-Trading 权重式仓位）
- 保留 20% 现金储备

**卖出规则：**
- 固定止损（保守 6%，成长 5%，短线 4%）
- 分批止盈：+4% 卖 30%，+8% 卖 60%
- 移动止损：盈利 2% 激活，ATR × 1.2 跟踪

## 使用方法

**下载历史数据：**
```bash
node fetch_tv_data.js --from 2025-01-01 --to 2025-06-30
```

**运行回测：**
```bash
python3 backtest.py --local --from 2025-01-01 --to 2025-06-30
```

**查看可视化报告：**
```bash
open backtest_report.html
```

**启动实时面板（需要 moomoo OpenD）：**
```bash
streamlit run dashboard.py
```

## 主要文件

- `backtest.py` — 回测引擎
- `portfolio_bot.py` — 实盘模拟调度器
- `strategy_config.py` — 三桶参数配置
- `strategy_signals.py` — 信号检测（金叉/回踩/突破/ADX）
- `execution_policy.py` — 仓位计算
- `fetch_tv_data.js` — TradingView 数据下载
- `dashboard.py` — Streamlit 监控面板
- `historical_data/` — 83 只股票日线数据
