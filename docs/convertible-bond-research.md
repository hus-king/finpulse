# 可转债研究

从主导航打开「可转债研究」。页面展示本周的研究持仓、首选候选、换仓门槛、候选明细和历史决策。研究持仓按独立记录中的模拟成交更新，与「模拟盘」账户分开。候选的涨跌空间来自正股涨跌 10% 的情景测算。

## 采集和发布

本地默认把研究数据存到 `data/cb-research/research.sqlite3`。可用 `FINPULSE_CB_RESEARCH_PATH` 指定其他位置。网页只读取已保存的数据，运营者通过命令采集或导入。

```bash
python -m backend.cb_research collect
python -m backend.cb_research publish /path/to/weekly-snapshot.json
```

预览可在首次正式发布前查看行情和数据缺口。正式决策要求使用已确认收盘的行情，通常在每周最后一个交易日生成。已核实的持仓风险事件可以单独发布退出信号。采集和发布应在知晓数据的时点执行，复盘时保留当时已知的评级、条款和事件。

导入 JSON 的顶层包含 `as_of`（上海交易日，`YYYY-MM-DD`）、`close_confirmed`、`sources`、`warnings` 和 `rows`。正式导入需要 `close_confirmed: true`。`sources` 和 `warnings` 是文本数组。每行包含：

- `code`（如 `123456.SZ`）、`name`、六位 `stock_code`、`stock_name`、`industry`。
- `price`、`conversion_value`、`stock_price`、`conversion_price`。债价和转股价值都按每 100 元面值，转股价值以同日不复权正股价及当日有效转股价计算。
- `rating`、`maturity_date`、`turnover_20d`（20 交易日成交额中位数，人民币元）、`stock_return_20d`、`industry_return_20d`（含分红复权的同口径 20 交易日收益，使用小数）。
- `tradable`、`risk`（`clear`、`call`、`credit`、`exit` 或 `unknown`）、`risk_reason`。`call` 表示已经公告实施强赎。`scenario_call_sensitive: true` 表示情景可能改变强赎退出路径，跳过排名。可选 `last_trade_date`、`last_conversion_date`、`source_url`。

缺少可核验信息的字段留空。只有当周条款和信用状态有依据时才标记为 `clear`。当前公开数据预览可以查看行情和基础资料，完整排序还需要可信的股债日线、行业收益和风险资料。默认研究金额为 20 万元，筛掉该金额超过 20 日成交额中位数 1% 的券。默认换仓成本假设为 0.4%，发布时可通过 `--order-size` 和 `--switch-cost` 更改。

## 记录模拟成交

使用交易日历验证信号的下一交易日。发布新一周前，记录上一条待执行信号的结果。换仓按同一交易日两笔均可成交的模拟口径处理，分别填买价和卖价。首次建仓只填买价，风险退出只填卖价。费用为仓位比例，例如 `0.004` 表示 0.4%。两笔交易均确认后才更新持仓。无法执行时保留持仓并写明原因。

```bash
python -m backend.cb_research record-execution 2026-11-06 2026-11-09 --sell-price 114 --buy-price 113 --cost 0.004
python -m backend.cb_research record-execution 2026-11-06 2026-11-09 --blocked "原券停牌"
```

以上价格和日期仅为命令示例。运行时使用真实的交易记录或模拟所依据的可成交数据。当前页的验证范围是规则、信号和模拟执行记录，策略历史表现需要用完整历史券池独立验证。
