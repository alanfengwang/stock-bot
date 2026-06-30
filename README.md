# Stock Research Bot

基于 `moomoo OpenD` 行情的本地量化研究与模拟交易系统，**不是实盘自动交易机器人**。

## 重要声明

- 所有交易通过 `local_broker.py` 本地虚拟撮合，**不调用 `place_order()` 做真实下单**
- 持仓、盈亏、手续费写入本地 `virtual_account.json` 和 `trade_log.csv`
- 适用于策略研究、参数实验、回测和可视化，不可直接接券商账户运行

## 回测结果（2025 H1）

| 指标 | 改进前 | 改进后 |
|------|--------|--------|
| 总盈亏（$1M本金） | $5,654 | **$195,806** |
| 收益率 | 0.57% | **19.6%** |
| 胜率 | 68% | **73%** |
| 交易笔数 | 683 | 364 |

数据来源：TradingView（无需 Futu OpenD）；期间：2025-01-01 → 2025-06-30

## 主要改进（来自 Vibe-Trading 开源项目学习）

- **权重式仓位**：`equity_pct_qty()` 替代 ATR/$200 风险公式，每笔使用 per-stock 分配额的 55-70%
- **ADX 趋势过滤**：金叉信号加 ADX ≥ 18 确认，过滤震荡行情中的假信号
- **TradingView 数据管道**：`fetch_tv_data.js` 无需 OpenD 在线即可下载历史 K 线

## 文件结构

### 核心入口

| 文件 | 说明 |
|------|------|
| `portfolio_bot.py` | Live 模式调度器，负责筛选、监控、信号执行和风险控制 |
| `backtest.py` | 历史回测入口，导出回测摘要与交易明细 |
| `dashboard.py` | Streamlit 监控面板 |
| `build_micro.py` | 一次性广泛建立微底仓 |

### 数据获取

| 文件 | 说明 |
|------|------|
| `fetch_tv_data.js` | 从 TradingView 下载历史 K 线（无需 OpenD）|
| `fetch_futu_data.py` | 从 Futu OpenD 下载历史 K 线（备选）|
| `historical_data/` | 83 只股票 2025 年日线 CSV 数据 |

### 共享策略模块

| 文件 | 说明 |
|------|------|
| `strategy_config.py` | 策略参数、股票宇宙、桶配置 |
| `strategy_signals.py` | 技术指标（含 ADX）、入场信号检测 |
| `execution_policy.py` | 仓位预算规则（equity_pct_qty） |
| `exit_rules.py` | 止损、保本线、移动止损 |
| `performance.py` | 胜率、利润因子、Sharpe 等绩效指标 |
| `trade_costs.py` | 手续费与滑点模型 |
| `market_utils.py` | 实时价格与行情工具 |
| `fundamental_store.py` | SEC 基本面缓存 |
| `fundamental_model.py` | 分行业基本面评分模型 |

## 策略说明

### 三桶结构

| 桶 | 股票类型 | 仓位比例 | 均线 | 附加指标 |
|----|---------|---------|------|---------|
| 保守（conservative）| AAPL/MSFT/GOOGL 等蓝筹 | 65% | 10MA / 50MA | RSI |
| 成长（longterm）| NVDA/AMD/TSM 等半导体 | 70% | 5MA / 20MA | MACD |
| 短线（shortterm）| TSLA/PLTR/OKLO 等高波动 | 55% | 5MA / 20MA | 量比 |

### 买入信号

- `golden_cross`：新金叉 + ADX ≥ 18 趋势确认
- `trend_pullback`：上升趋势中回踩快线后重新站上
- `breakout`：突破近期高点 + 放量
- `pyramid_stage2/3`：盈利 +5%/+12% 后金字塔加仓

### 卖出规则

- 固定止损（保守 6%，成长 5%，短线 4%）
- 分批止盈（+4% 卖 30%，+8% 卖 60%）
- 移动止损（盈利 2% 后激活，ATR × 1.2）
- 死叉 / RSI 超买 / 时间止损

## 快速开始

### 1. 下载历史数据（TradingView，无需 OpenD）

```bash
node fetch_tv_data.js --from 2025-01-01 --to 2025-06-30
```

### 2. 运行回测

```bash
python3 backtest.py --local --from 2025-01-01 --to 2025-06-30
```

### 3. 查看可视化报告

```bash
open backtest_report.html
```

### 4. 启动实时监控面板（需要 OpenD）

```bash
streamlit run dashboard.py
```

## 技术依赖

```bash
pip install moomoo-api pandas streamlit
# Node.js + TradingView-API 子目录（用于 fetch_tv_data.js）
```
