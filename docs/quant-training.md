# 官方量化研究版：训练、策略接口与执行

首版为 PyTorch 12→32→16→1 的小型回归网络，用过去65根完整日线计算12个量价特征，预测下个交易日开盘起约5日的价格收益。训练在 `husking194` 完成，线上只读取 JSON 数值权重，用纯 Python 推理。官方仅表示由项目维护；当前版本是研究实验，样本外结果没有盈利。

## 可复现产物

- `models/official_quant/model.json`：标准化参数、特征版本、数值权重、股票池、阈值和模型版本。
- `models/official_quant/report.json`：模型 SHA-256、每只股票数据 SHA-256、来源与覆盖、日期划分、依赖版本、阈值验证结果、最终测试净值和基准。
- 本轮历史原始数据及复权参照保存于训练机 `/home/husking/finpulse-quant/20261021/data/`，未公开分发原始行情。共30只当期固定股票池，存在幸存者与选择偏差。

```bash
# 在自己的独立任务目录准备 backend/ 与 scripts/quant/；先检查磁盘和 GPU。
PYTHONDONTWRITEBYTECODE=1 python3 -m scripts.quant.run_guarded --root . \
  /path/to/cuda-compatible/python -u -m scripts.quant.pipeline --root . --end 2026-10-20
```

任务目录最多2 GiB；监督器每秒检查进程树 RSS / GPU 占用和运行时长，超过3 GiB RAM、3 GiB GPU、1小时或磁盘余量低于20 GiB时仅终止本次进程组。CPU限制为1核、nice19、ionice idle，数学库单线程；PyTorch allocator最多2 GiB。监督器为轮询保护，可能有短暂超限，不等价于内核硬内存配额；显存allocator限额不涵盖CUDA上下文，因此另监测实际显存。GPU余量不足6 GiB不启动；训练期间余量下降到4 GiB停止。不要对其他进程使用 killall/pkill。

本轮只读复用 `/home/husking/finpulse-experiments/laya-gpu-20261009/.venv/bin/python`，没有安装或覆盖任何其他环境。其 PyTorch 2.7.1+cu128 与驱动兼容；fastai 环境的 cu130 不兼容，因此未用于训练。

按全体股票共同日期切分60%训练、20%验证、20%测试，清除标签跨界样本。标准化仅拟合训练集；早停和三个固定阈值只用验证集。最终测试不用于调参，部署的是同一份被测试的模型，没有事后用测试区间重新训练。

回测是未复权模拟价格损益，下一日开盘成交近似线上10点分钟成交；已计当前模拟费率与双边10bp滑点。公司行为以腾讯前复权与原始OHLC之间的分段仿射关系检测，允许上游分位精度误差。训练过滤特征及标签跨公司行为的窗口；测试交易只按信号日已知历史过滤，绝不预知未来除权日。持仓跨公司行为时不补发分红/送转，事件次数单列，所以结果并非精确的真实总回报，也不能宣称可逐笔复制。基准使用相同费用、滑点和风险暂停；等权基准在30只股票间配置80%，模型与动量最多5只。

## 添加官方算法

实现 `backend.quant.strategy.Strategy` 的 `metadata()`、`validate_config(config)` 和 `decide(histories, as_of)`，通过 `StrategyRegistry.register(id, adapter)` 注册。当前执行适配器读取 `metadata()` 返回的 `universe` 与 `version`，不要求特定模型内部结构，历史输入为代码→完整未复权日线，输出至少包含：

```json
{"as_of":"2026-10-20","model_version":"custom-v1","target_weights":{"000001":0.2},"reason":"策略解释"}
```

策略只返回目标权重；持久化、风控、调度与成交由执行器负责。目标最多5只、单票20%、总仓80%，且只能选择已注册股票池。可信官方模型来自发布目录；客户可上传受限JSON规则，通过同一策略适配接口运行，详见[客户提交规范](strategy-submission.md)。不执行用户Python/JavaScript或可执行模型。

账户接口（都需要登录，写入需要现有 CSRF 校验）：

- `GET /api/paper/strategies`：官方与本人策略目录，`origin` 标记来源；官方策略附研究报告。
- `GET /api/paper/automation`：操作方式、配置版本、调度开关和最近状态。
- `PUT /api/paper/automation`：`mode=manual|algorithm`、`strategy_id`、`expected_revision`；本人策略还需 `strategy_revision`；版本冲突409。
- `GET /api/paper/automation/decisions?before=...&limit=20`：账户隔离的决策分页。

托管状态、决策、共享历史缓存、当日成交预算分别保存到 `quant_config`、`quant_decision`、`quant_history`、`quant_day` 命名空间。模型缺失或校验失败时不可启用。账户和成交仍复用原表与用户行锁，不需要新DDL。

## 调度与恢复

只有显式设置 `FINPULSE_QUANT_ENABLED=1` 的实例运行调度。服务器每分钟检查，核验交易日北京时间10点后执行当日一次决策，节假日/未知日历/盘后不补单。网页关闭不影响托管。多实例用数据库租约，配置切换和下单复用用户锁；暂停返回后旧配置不能产生新成交。

决策先保存完整订单意图和确定性UUID；成交、当日预算和决策已用预算在同一事务提交。进程中断后读取原计划，不按新现金重算旧UUID；成功订单先重放再检查市场和配置。新订单重新检查模式/版本、租约、账号状态、行情、T+1、现金与风险限制。切换操作方式不会重置当日30%成交预算。

报价缺失暂停；近期公司行为窗口跳过；ST、退市标记、无成交量或报价距离昨日收盘达到4.5%时保守跳过。无法模拟真实盘口排队或保证流动性。现有股票池外持仓保留且计入风险额度；缺失持仓报价时不新增风险。15%回撤触发暂停，无法保证实际最大回撤不超过15%。

## 发布和回退

新快照必须同时包含代码、dist与两份模型JSON，先验证checksum、完整回归和构建。保留私有配置/数据库，沿用 current symlink 与 finpulse.service。回退到旧版本时先禁用 `FINPULSE_QUANT_ENABLED`，再切换快照，保留托管配置与成交记录供审计。
