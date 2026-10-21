# Official Quant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 训练可复现的官方日频量价模型，接入现有模拟盘并发布。

**Architecture:** GPU 离线训练小型 MLP，导出 JSON 数值权重供服务器纯 Python 推理。策略接口只输出目标权重；服务负责调度、账户配置、风控，所有成交通过现有事务执行。

**Tech Stack:** PyTorch (仅训练)、Python/FastAPI、SQLite/MySQL、React/TypeScript。

**Spec:** docs/superpowers/specs/2026-10-21-official-quant-design.md

## Global Constraints

- 不影响已有 husking194 进程；单 CPU、3 GiB RAM、60 分钟、2 GiB 目录、2 GiB 张量显存 / 3 GiB 实际显存。
- 默认手动，调度仅服务器显式开启；持仓不因切换清空。
- 日频最多五只股票、单票20%、总仓80%、每日成交30%、峰值回撤15%暂停。
- 数据按共同日期划分 60/20/20，跨界标签清除；样本外结果如实报告。
- 所有交易保留鉴权、CSRF、用户锁、幂等、分钟价格、T+1 和费用规则。

## Review Focus

- 暂停成功后旧信号不得成交；在成交事务内检验配置版本与租约。
- 部分成交后重启不得按变化的现金重新解释旧请求号；持久化完整订单意图。
- 有旧持仓和缺失报价的账户不能绕过组合总仓位及峰值回撤控制。
- 股票池数据日期不一致、损坏模型、缺少数据时不可把异常当作空仓信号。
- 多实例租约超时和停用用户必须在落单时拒绝。

## Task 1: 特征、策略契约与安全模型加载

**Files:** backend/quant/{__init__,features,strategy}.py; tests/test_quant_model.py; models/official_quant/{model,report}.json
**Interfaces:** features(candles)->list[float]; QuantStrategy.decide(histories,as_of)->dict; metadata()->dict; registry.get(id)->strategy。
- [ ] 先写并运行特征历史切片、有限数值、训练推理一致性、校验和/版本拒绝与权重上限测试，预期失败。
- [ ] 实现12个量价特征、纯 Python MLP 前向推理和注册接口，校验维度及文件摘要。
- [ ] 运行 `python -m unittest discover -s tests -p test_quant_model.py`，预期全部通过；提交。

## Task 2: 数据、GPU 训练与回测

**Files:** scripts/quant/{collect,train,run_guarded}.py; tests/test_quant_training.py; docs/quant-training.md。
**Interfaces:** collect.fetch_history(code,start,end,adjust)->candles; train.make_samples(data)->rows; split_samples(rows)->partitions; backtest(data,predict,dates)->metrics。
- [ ] 测试共同日期分割、标签禁跨界、下一开盘成交、费用/滑点与公司行为窗口过滤，运行确认失败。
- [ ] 实现腾讯日线有限采集、清单与校验和、GPU MLP 训练早停和验证选择、最终测试报告、JSON 权重导出。
- [ ] 实现任务树资源监督器；在 husking194 核验可用资源与限额，启动受限的采集/训练任务。
- [ ] 核验真实数据与产物，复制小型产物并验证纯 Python/GPU 推理误差；提交。

## Task 3: 账户托管与自动执行

**Files:** backend/quant/{store,service,api}.py; backend/paper_store.py; backend/paper_trading.py; backend/app.py; tests/test_quant_automation.py。
**Interfaces:** QuantStore.configure(owner,mode,strategy_id,expected_revision); QuantService.tick(); GET/PUT automation; GET strategies/decisions。
- [ ] 测试模式切换、CSRF、账号隔离、暂停竞态、停用用户、租约、重放、重启与持仓风控，运行确认失败。
- [ ] 复用用户行锁存配置、决策和完整订单意图；在成交事务内校验模式、版本、有效租约及组合上限。
- [ ] 实现上一完整日数据检查、后台10点后每日执行、确定性订单号、部分成功恢复、限仓与回撤暂停；非生产默认关闭。
- [ ] 运行量化和模拟盘后端测试全部通过；提交。

## Task 4: 模拟盘界面

**Files:** src/{PaperAutomation,PaperTradingPanel}.tsx; src/{paperTypes,api}.ts; src/paper.css; tests/paper_ui.mjs。
**Interfaces:** api(path,body,signal,method); PaperAutomation(onModeChange,disabled)。
- [ ] 确认自动模式显示策略报告、暂停与错误原因；手动新单在前后端均禁用，未确认请求可重放。
- [ ] 实现策略切换、运行/决策状态、样本外摘要、来源标记与响应式布局。
- [ ] 执行生产构建及隔离浏览器检查；提交。

## Task 5: 发布与验收

**Files:** docs/paper-trading.md; docs/quant-training.md; docs/superpowers/reports/2026-10-21-official-quant-release.md; deploy/service.env.example。
- [ ] 运行全部后端/Node 回归和前端构建，通过后请求独立分支审查，修复重要问题。
- [ ] 打包固定版本、前端和模型，新 release 先检查依赖、产物与接口；保留数据库和私有配置。
- [ ] 显式开启量化调度，切换 current，仅重启 FinPulse，检查健康、策略报告及登录后模式接口；失败恢复旧 release。
- [ ] 保存训练资源、数据与回测实测、验收结果、发布/回退路径；提交。
