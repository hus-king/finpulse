# Customer JSON Rule Strategies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** 网页与API支持客户上传、管理并托管运行统一JSON规则策略。
**Architecture:** 严格有界DSL→RuleStrategy适配器；owner策略存储和解析复用现有账户锁与模拟执行器；UI管理JSON不运行用户代码。
**Tech Stack:** Python/Pydantic/FastAPI、research_records、React/TypeScript。
**Spec:** docs/superpowers/specs/2026-10-21-customer-rule-strategies-design.md

## Global Constraints

包64KiB、10策略/账号、写操作20次/分钟、股票1–30只、表达式合计128节点/深度6/有限常数≤1e6、12筛选、最多5持仓/单票20%/总仓80%；仅本人CRUD，CSRF，CAS，托管中改删拒绝；不使用eval/exec或上传Python。

## Review Focus

- 深嵌套或超大JSON在解析前受到限制，布尔/NaN/无穷数不可作为权重。
- 别人的ID、官方ID、重复版本不能覆盖本人或官方策略。
- 策略正在自动下单时编辑/删除与配置检查使用同一用户锁。
- 规则错误不能变成清仓信号，也不能产生收费API调用。
- 自定义策略保留官方执行器全部限额与暂停幂等语义。

## Task 1: 严格策略包与解释器
Files: backend/quant/rules.py; tests/test_user_strategy_rules.py
Interfaces: RulePackage; validate_package(dict)->dict; RuleStrategy(package, id, revision, digest).metadata()/decide(histories,as_of).
- [x] 写真实评分、过滤、排序、节点/深度/字段/有限数限制和除零失败测试；运行确认RED。
- [x] 实现有界表达式和统一包、规范化SHA256、示例及schema；运行测试GREEN；提交。

## Task 2: 账号存储与HTTP规范
Files: backend/quant/user_store.py; backend/quant/user_api.py; backend/app.py; tests/test_user_strategy_api.py
Interfaces: UserStrategyStore.create/get/list/update/delete(owner, ..., expected_revision); schema/validate/CRUD端点。
- [x] 测试登录/CSRF/owner/请求体/限额/CAS/停用/正在托管限制RED。
- [x] 实现用户行锁CRUD、私有目录、请求大小中间件和20次/分钟限额；GREEN；提交。

## Task 3: 托管解析与执行
Files: backend/quant/service.py; backend/quant/api.py; backend/quant/strategy.py; tests/test_user_strategy_execution.py
Interfaces: QuantService.strategy(owner,id); strategies(owner)；官方路径保持兼容。
- [x] 测试用户策略选择、隔离、共同风控、暂停/修改竞态、重启重放RED。
- [x] 将owner-aware解析接入配置和每日执行，元数据来源标记；GREEN；提交。

## Task 4: 网页策略管理
Files: src/UserStrategyEditor.tsx; src/PaperAutomation.tsx; src/paperTypes.ts; src/api.ts; src/paper.css; tests/user_strategy_ui.mjs
- [x] 浏览器复现尚无管理入口；实现示例/导入/校验/保存/编辑/导出/删除，选择本人策略启用/暂停。
- [x] 验证来源标记、错误提示、托管期间保护、移动端和无收费副作用，构建通过；提交。

## Task 5: 规范文档与发布
Files: docs/strategy-submission.md; examples/strategies/momentum-v1.json; README.md; docs/quant-training.md。
- [x] 导出可下载JSON Schema与示例、接口/错误码/限额/SDK调用说明。
- [x] 完整Python/Node/构建和浏览器回归；独立审查并修复。
- [ ] 合并main、推送GitHub、部署同提交、健康/模型/受保护接口/用户策略验收，保存回退元数据。

验证记录：独立只读审查未发现 Critical/Important 问题；完整 Python 368 项与新增规范一致性测试通过，Node 56 项、生产构建、模拟盘/官方量化/持仓导航/客户策略四组浏览器验收通过。最新完整回归及发布证据保存于本机 .runtime/customer-*；发布状态由生产 shared/release.json 记录。
