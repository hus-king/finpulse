# FinPulse 20 万元 A 股模拟盘 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在现有 FinPulse 网站上线每人 20 万元的独立 A 股模拟盘，完成买卖、T+1、持仓盈亏及成交记录。

**Architecture:** 独立交易规则模块使用整数分计算，持久化模块复用 research_records 并在一个事务内保存账户和成交。服务和 API 沿用现有身份、股票库及分钟行情；React 模拟盘页面使用已有 CSRF 请求封装和主题变量。

**Tech Stack:** Python / Decimal、FastAPI、SQLite / MySQL、React 19、TypeScript、Vite、unittest、Node test runner、Playwright。

**Spec:** [已确认设计](../specs/2026-10-08-paper-trading-design.md)

**Execution:** 当前会话按任务顺序执行；每步验证通过后独立提交，最后统一审查和发布。用户已要求“分步执行多次 commits”。用户已确认本计划，按步骤执行中。

## Global Constraints

- 初始资金为每个账号 20 万元，即 20,000,000 分；持久化金额为整数分，费率计算使用 ROUND_HALF_UP。
- Asia/Shanghai；核验交易日 09:30 ≤ 时间 < 11:30、13:00 ≤ 时间 < 14:57；未知日历禁止成交；T+1 禁售当日买入股份。
- 主板／创业板买入为 100 股整数倍；科创板至少 200 股并可逐股增加；零股规则及数量上限按 spec。
- 仅服务器有效未复权 1 分钟价格成交；分钟年龄 ≤180 秒、最多领先 60 秒，采集年龄在 0～90 秒；须为当前交易日且无行情错误。
- 固定模拟佣金 0.03% 最低 5 元；双向过户费 0.001%；卖出印花税 0.05%；各项分别舍入到分。
- 一个事务保存现金、持仓和成交；用户行锁保护首次初始化和并发；成功交易按用户加 UUID 幂等。
- 复用现有表、认证、Origin、CSRF、行情并发限制；不添加产品依赖、不修改私有配置或开机启动设置。
- 显示虚拟资金与分钟报价局限；深浅主题、小屏布局、两位小数；搜索不能触发新闻或 AI 采集。
- 本地编辑、验证后同步 GitHub，再发布到既有服务器；保留可回滚发布目录。

## Review Focus

- 网络响应丢失后收盘再重试：取得原成交，现金只扣一次；Task 2、3、4 验证。
- 切换账号或快速切换股票：不能露出旧账户信息或提交旧股票报价；Task 4 浏览器验证。
- 上午报价被下午新采集重新缓存、分钟标签在未来：不能仅凭 fetched_at 判定可成交；Task 1、3 验证。
- 部分卖出产生分摊舍入残差：清仓后成本必须归零且资金守恒；Task 1、2 验证。
- 账号在认证通过后被禁用：写事务内再次检查，不能落下成交；Task 2、3 验证。

## 文件与接口约定

新增 backend/paper_rules.py（规则、费用、成交和估值计算）、backend/paper_store.py（事务与历史查询）、backend/paper_trading.py（编排与行情）、backend/paper_api.py（验证、权限及响应）。仅修改 backend/app.py 的生命周期和路由注册。

新增 src/paperTypes.ts（契约）、src/paperMath.ts（预估与格式化）、src/PaperTradingPanel.tsx（账户页面）、src/PaperStockSearch.tsx（独立选择股票）、src/paper.css（响应式主题样式）。修改 src/App.tsx 接入导航。新增对应 Python 测试、tests/paper_math.test.mjs、tests/paper_ui.mjs（浏览器脚本）和 tests/paper_ui_server.py（隔离测试服务）；更新 README.md 和 docs/paper-trading.md 使用说明。

所有 API 金额字段以 _fen 结尾。账户响应包含 initial_cash_fen、cash_fen、market_value_fen、equity_fen、realized_pnl_fen、unrealized_pnl_fen、total_pnl_fen、return_percent、positions、market。持仓包含 code、name、quantity、sellable、locked、cost_fen、average_cost_fen、price_fen、price_as_of、valuation_status、market_value_fen、unrealized_pnl_fen。报价响应包含 stock、rules、price_fen、as_of、fetched_at、source、market、can_trade、reason_code、reason、refreshing、next_poll_seconds。成交响应包含 request_id、sequence、code、name、side、quantity、price_fen、gross_fen、commission_fen、transfer_fen、stamp_fen、fees_fen、cash_after_fen、realized_pnl_fen、executed_at、quote_as_of、quote_fetched_at、source。历史响应为 items、next_before_seq。

### Task 1: 交易规则与精确账务

**Files:** Create backend/paper_rules.py; Test tests/test_paper_rules.py。

**Interfaces:**
- INITIAL_CASH_FEN = 20_000_000；PaperError(code: str, message: str, status: int = 409)。
- new_account() -> dict；board_rules(code: str) -> dict；session_state(now: datetime) -> dict；sellable_quantity(position: dict, now: datetime) -> int。
- fees(gross_fen: int, side: str) -> dict；valid_quote(cached: dict, now: datetime) -> dict，返回 price_fen、as_of、fetched_at、source，无有效报价时抛出 PaperError。
- apply_trade(account: dict, stock: dict, side: str, quantity: int, quote: dict, now: datetime) -> tuple[dict, dict]，输入保持不变；返回新账户和待补充幂等键的成交记录。
- value_account(account: dict, quotes: dict[str, dict], now: datetime) -> dict；quotes 缺失时按持仓最近成交价估值并标记 estimated。

- [x] 写初始余额、买入费用和 T+1 测试；关键断言为 `new_account()['cash_fen'] == 20_000_000`、`fees(100_000, 'buy')['fees_fen'] == 501`、同日可卖 0、下一交易日可卖 100。
- [x] 运行 `.venv/bin/python -m unittest discover -s tests -p 'test_paper_rules.py' -v`，确认因模块或函数缺失失败。
- [x] 实现上述纯函数；日期批次维护数量，成本按平均成本分摊，最后清仓扣尽成本；规则失败有稳定业务码。
- [x] 增加时段边界（09:29:59、09:30、11:30、13:00、14:57）、国庆／周末／未知年份、100 与 200 股规则、上限、零股、资金不足与超卖测试。
- [x] 增加报价错误、价格 NaN／无穷／零／负数、昨日／午休旧报价、采集未来、分钟领先 60 与 61 秒、年龄 180 与 181 秒、采集年龄 90 与 91 秒测试。
- [x] 增加多次买入和部分卖出、费用边界及清仓测试；断言清仓 cost_fen 为 0、现金非负、现金减初始金额等于累计已实现盈亏。验证延迟与 estimated 估值不成为可成交报价。
- [x] 重跑规则测试，全部通过后提交 `feat: add precise A-share paper trading rules`。

### Task 2: 持久化、并发与幂等

**Files:** Create backend/paper_store.py; Test tests/test_paper_store.py。

**Interfaces:** PaperStore(research_store: ResearchStore)。get_account(owner: str) -> dict 返回保存值或 new_account()；list_trades(owner: str, limit: int = 20, before_seq: int | None = None) -> dict；transact(owner: str, request_id: str, intent: dict, apply: Callable[[dict, dict], tuple[dict, dict]]) -> dict，回调接收账户和该 code 的原始分钟缓存。回调在锁内执行；存储层补齐 request_id、sequence。

- [x] 写临时 AuthStore / ResearchStore 测试，注册两个测试用户；断言新账户均为 20,000,000 分、读取不创建记录、首笔成交更新且另一个用户不变。
- [x] 运行 `.venv/bin/python -m unittest discover -s tests -p 'test_paper_store.py' -v`，确认缺失实现失败。
- [x] 实现用户行锁、锁内有效性检查、请求内容比较、账户和成交同事务写入；SQLite／MySQL 分别使用现有连接 API 和 upsert 方言。不调用独立 ResearchStore.put 写资金。
- [x] 增加相同 UUID 重放、UUID 内容冲突、重建存储实例后恢复，以及成交失败不创建资金记录测试。
- [x] 用 ThreadPoolExecutor 验证同 UUID 并发只成交一次、两个不同请求抢有限现金只成功一个、并发卖出不超卖；故障注入在账户更新后、成交插入前抛异常，断言账户和记录全部回滚。
- [x] 验证禁用账号不能写入；按 sequence 取得同秒多笔交易及分页，无重复遗漏。SQL 按 JSON 中的数值 sequence 排序与过滤，兼容两种数据库；补充 MySQL 连接替身验证 FOR UPDATE 与写入共用一连接。
- [x] 重跑存储及规则测试，全部通过后提交 `feat: persist isolated atomic paper accounts and trades`。

### Task 3: 服务、身份 API 与生命周期

**Files:** Create backend/paper_trading.py、backend/paper_api.py; Modify backend/app.py; Test tests/test_paper_api.py。

**Interfaces:** PaperTradingService(store: PaperStore, catalog, minute_market, clock=None)。account(owner: str) -> dict；trades(owner: str, limit: int, before_seq: int | None) -> dict；async quote(code: str, refresh: bool = False) -> dict；async submit(owner: str, request_id: str, code: str, side: str, quantity: int) -> dict。clock 默认 datetime.now(SHANGHAI)。API 通过 asyncio.to_thread 执行同步数据库工作；存储回调内部重新读取 clock。

- [x] 用 TestClient 和隔离 SQLite 写 GET /api/paper/account、GET /api/paper/quote/000001、GET /api/paper/trades、POST /api/paper/trades 测试，未登录均 401；登录读接口返回 no-store 和 20 万元。
- [x] 运行 `.venv/bin/python -m unittest discover -s tests -p 'test_paper_api.py' -v`，确认新增路由尚不存在导致失败。
- [x] 实现 PaperTradeRequest：UUID、六位 code、Literal buy/sell、严格正整数 quantity、extra forbid；接入 current_session 和 authorize_model_request，PaperError 映射为 detail、code 与状态码。
- [x] 在生命周期接入 PaperStore 与 PaperTradingService、注册路由；报价端点只安排现有分钟刷新，账户估值只读缓存，不调用日线 bundle、新闻或模型。
- [x] 写 Origin、CSRF、伪造 owner／price、布尔／小数／负股数、未知股票、历史分页参数测试；断言无效写入不改变账户。
- [x] 用受控时钟和行情缓存测试真实买入、同日卖出拒绝、翌日卖出、休市拒绝、过期报价拒绝及缺报价估值；断言模型／新闻 fetcher 未调用。
- [x] 模拟提交前行情新鲜但锁内时钟跨过 14:57，必须拒绝；成功后移动到收盘时间并重发相同 UUID，必须返回原记录。模拟认证后禁用账号，必须拒绝事务写入。
- [x] 运行 `.venv/bin/python -m unittest discover -s tests -p 'test_paper_*.py' -v`，全部通过后提交 `feat: expose authenticated paper trading API`。

### Task 4: 模拟盘页面与交易交互

**Files:** Create src/paperTypes.ts、src/paperMath.ts、src/PaperStockSearch.tsx、src/PaperTradingPanel.tsx、src/paper.css; Modify src/App.tsx; Test tests/paper_math.test.mjs、tests/paper_ui.mjs、tests/paper_ui_server.py。

**Interfaces:** PaperTypes 按本计划契约声明；PaperRules 包含 board、min_quantity、quantity_step、max_quantity。estimateFees(grossFen: number, side: 'buy'|'sell') -> {commission_fen, transfer_fen, stamp_fen, fees_fen: number}，整数比率舍入；maxBuyQuantity(cashFen: number, priceFen: number, rules: PaperRules) -> number，计入费用和上限；formatMoney(fen: number | null) -> string。PaperStockSearch({ onSelect: (stock: Stock) => void })；PaperTradingPanel({ initialCode: string })，使用 useAuth、api，用户切换以 key={user?.id ?? 'guest'} 隔离。测试服务 create_app(db_path: Path) -> FastAPI，挂载同样身份／模拟盘路由及构建的 dist，使用临时 SQLite、受控时钟、固定股票与行情；测试专属时钟控制仅存在于测试模块，服务仅监听 127.0.0.1。

- [x] 写前端费用／最大可买量测试：10 元 ×100 股费用 501 分；20 万余额可以买入 100 股 ×1338 元，但余额不足时返回 0；科创板最小 200 股、可买 201 股，且均计入费用。Node 通过 TypeScript strip types 引入独立 math 文件。
- [x] 运行 `node --test tests/paper_math.test.mjs`，确认缺失实现失败。
- [x] 实现类型和 math 辅助函数，不在前端累积真实余额；实现股票搜索的取消、去抖和错误展示，仅调用 /api/stocks。
- [x] 实现账户、持仓、成交记录、股票报价、买卖表单和简明规则；休市或报价无效时禁用下单；显示费用、可买／可卖股数和锁定数量。提交期间锁定表单，网络结果未知时保留意图与原 UUID，可重试或查询历史核对，不能自动用新 UUID 再下单。
- [x] 在 App 接入模拟盘导航、标题和按账号挂载；加入主题变量样式、表格横向滚动、表单标签、状态／错误可访问反馈。报价轮询遵守 next_poll_seconds，切换标的和卸载取消旧请求；账户与成交分页分别维护加载状态。
- [x] 写浏览器脚本，启动 tests/paper_ui_server.py 隔离服务，用本地 API 场景检查 20 万显示、选股、买入成功、T+1 禁售、下一交易日卖出及记录；生产时间限制保持原样，只在测试服务器注入时钟。脚本支持 PAPER_UI_BASE_URL，默认 http://127.0.0.1:8012，运行 `node tests/paper_ui.mjs`，失败退出非零并保留截图；测试服务命令 `.venv/bin/python tests/paper_ui_server.py --port 8012`。
- [x] 浏览器模拟丢失一次成功成交响应，重试必须携带原 UUID 且只存在一笔成交；快速切换股票时旧报价不得覆盖，退出及切换账号不得显示旧持仓。
- [x] 运行 Node 测试、`npm run build` 和浏览器检查；检查深色、浅色、390 像素宽页面无整体横向溢出。通过后提交 `feat: add themed 200k paper trading workspace`。

### Task 5: 整体验证、说明与服务器发布

**Files:** Modify README.md; Create docs/paper-trading.md; 校正前述功能文件与测试（仅修复验证发现的问题）。

**Interfaces:** 使用现有部署目录 /home/airhust/finpulse-deploy/{releases,shared,current}、SSH airhust@202.114.212.116、finpulse.service、Nginx 8000；不新增部署服务或数据库表。

- [x] 写使用说明：20 万、规则、费用、分钟报价局限、休市行为、数据持久化、手动启动方式及对应测试命令；README 链接 docs，其他 Markdown 继续放 docs。
- [x] 运行 `.venv/bin/python -m unittest discover -s tests -v`、`node --test tests/chart_math.test.mjs tests/paper_math.test.mjs`、`npm run build`、`git diff --check`；输出全部测试成功且构建成功才进入发布。
- [x] 完整审查资金边界、事务、幂等、隔离和页面请求竞态；按用户选定执行方式完成所需独立审查。修复具体问题并重跑受影响检查，记录最终证据。
- [ ] 提交最终代码和说明；fetch origin 后确认 main 没有并行变更，必要时安全 rebase 并验证，不 force push；推送 GitHub。
- [ ] 检查服务器当前发布、服务状态和健康；打包 git archive、dist 和含 commit／静态资源 SHA256 的 release.json；传入新 release 目录，链接共享配置，保留旧静态资源以兼容已打开页面。
- [ ] 发布前再次确认 current 未被他人更新；原子切换 symlink，重启已有 finpulse.service，核对服务 active、/api/health、模拟盘接口访客 401 和首页资源哈希。失败则原子切回前一目录并重启验证恢复。
- [ ] 在上线页面检查新增入口、登录提示、主题和移动布局；实际成交场景用隔离测试证据，生产不伪造时钟或行情，不为验证创建生产账号或修改他人资金。
- [ ] 交付网站链接、上线提交、验证结果和报价／交易时段限制；附页面截图。到此任务完成。
