# Stock Research Bot

本项目是一个基于 `moomoo OpenD` 行情的本地研究/模拟交易系统，不是实盘自动交易机器人。

## Important

- 交易执行使用 [local_broker.py](/Users/apple/Documents/Stock/local_broker.py:1) 的本地虚拟撮合。
- 不调用 `OpenSecTradeContext.place_order()` 做真实下单。
- 所有仓位、盈亏、手续费都写入本地 `virtual_account.json` 和 `trade_log.csv`。
- 适用于策略研究、参数实验、回测和界面展示，不应被理解为可直接接券商账户运行的实盘 bot。

## Structure

- [portfolio_bot.py](/Users/apple/Documents/Stock/portfolio_bot.py:1): live 模式调度器，负责筛选、监控、信号执行和风险控制。
- [backtest.py](/Users/apple/Documents/Stock/backtest.py:1): 历史回测入口，复用共享信号和仓位逻辑，导出回测摘要与交易明细。
- [build_micro.py](/Users/apple/Documents/Stock/build_micro.py:1): 一次性广泛建立微底仓。
- [init_positions.py](/Users/apple/Documents/Stock/init_positions.py:1): 初始化每桶试探仓位。
- [dashboard.py](/Users/apple/Documents/Stock/dashboard.py:1): Streamlit 监控面板。

共享模块：

- [strategy_config.py](/Users/apple/Documents/Stock/strategy_config.py:1): 统一策略参数、股票宇宙、行业映射、研究常量。
- [strategy_signals.py](/Users/apple/Documents/Stock/strategy_signals.py:1): 技术指标、附加条件、事件检测。
- [execution_policy.py](/Users/apple/Documents/Stock/execution_policy.py:1): 统一仓位预算和事件对应的资金规则。
- [trade_costs.py](/Users/apple/Documents/Stock/trade_costs.py:1): 手续费与滑点模型。
- [performance.py](/Users/apple/Documents/Stock/performance.py:1): 胜率、利润因子、回撤、Sharpe 等绩效指标。
- [market_utils.py](/Users/apple/Documents/Stock/market_utils.py:1): 实时价格与行情请求小工具。

## Strategy Summary

买入事件：

- `golden_cross`: 新金叉，标准仓位。
- `trend_pullback`: 上升趋势中的回踩确认，较轻仓位。
- `breakout`: 突破近期高点，次标准仓位。
- `micro_position`: 分散底仓，固定 `$500` 级别，默认选 `5-10` 只跨板块股票。

仓位规则：

- 始终保留 `20%` 现金储备。
- 非底仓买入使用 ATR 风险约束和桶预算共同决定数量。
- `starter` / `add_position` 都受目标仓位缺口限制，不无限补仓。

卖出规则：

- 固定止损
- 移动止损
- 死叉/超买
- 盈利到阈值后的分批止盈

## Backtest Notes

- 回测与 live 端共用信号检测、仓位预算、手续费函数。
- 默认启用固定滑点模型，参数在 [strategy_config.py](/Users/apple/Documents/Stock/strategy_config.py:1)。
- 当前回测不重放 snapshot 基本面，因此不会完全复刻 live 端的基本面过滤。
- 回测结果会导出：
  - `backtest_summary.csv`
  - `backtest_trades.csv`

## Run

```bash
python3 build_micro.py
python3 build_micro.py --execute
python3 portfolio_bot.py
python3 backtest.py
streamlit run dashboard.py
```

## Tests

```bash
python3 -m unittest test_execution_policy.py test_trade_costs.py test_performance.py test_strategy_signals.py
```
