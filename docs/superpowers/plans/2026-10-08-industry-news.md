# 行业新闻 Implementation Plan

**Goal:** 为所有可搜索的沪深 A 股自动获取所属行业新闻，保留来源、日期和与目标公司的关联依据。

**Architecture:** 公司新闻保留现有采集流程。新增行业资料与行业查询服务，复用 research_records 的 JSON 存储、数据库租约、Tavily 与 AkShare 子进程。规则生成通用查询和可选行业因素，独立清洗行业新闻，研判和缓存按目标公司隔离。

**Tech Stack:** Python / FastAPI / unittest，React / TypeScript，现有 SQLite / MySQL。

**Spec:** 本次对话已确认的通用行业方案及用户“帮我实现”的执行授权。

## Global Constraints

- 覆盖新搜索的任意沪深 A 股；无专门规则的已识别行业仍有通用查询。
- 只读取实际源数据；行业未知不猜测，不将“A 股”作为行业。
- 每轮最多新增两组行业查询；自动研判默认仍为 3 条，优先 2 公司 + 1 行业、不足互补。
- 行业资料缓存 7 天，行业搜索成功缓存 30 分钟，失败退避 5 分钟；同主题/区间跨股票共享。
- 行业新闻须有日期、独立文章和相关事件证据，行情/公司介绍/无关内容继续过滤。
- 缓存失败保留旧内容并标明状态；行业源失败不影响公司新闻。
- 保留市场情绪和社区功能改动；用户后续授权审查后提交、推送及部署，不手动发送消息推送。

## Tasks

### 1. 行业规则、资料与共享查询

- [x] 创建 tests/test_industry_news.py，验证任意行业的通用查询、各行业因素、资料身份校验、未知行业、缓存重启复用、失败保留、跨实例租约和并发上限。
- [x] 运行新测试观察失败。
- [x] 新增 backend/industry.py（规则/关联匹配/选取预算）和 backend/industry_news.py（资料与查询服务）；给 providers.py 和 akshare_worker.py 增加行业信息适配器。
- [x] 运行新测试确认通过。

### 2. 清洗、研究和晨报链路

- [x] 补测试：不含股票名的行业事件保留；标题/正文关联证据、日期冲突、无关帖子、重复公司/行业新闻；行业源失败保留旧行业新闻；不同公司和不同资料版本隔离研判缓存；选择预算和晨报合并。
- [x] 运行测试观察失败。
- [x] 修改 news_cleaning.py、research.py、prompts.py、recommendations.py、app.py；保留既有接口和旧记录兼容，增加可选 news_scope / industry / related_factors / relevance_reason / industry_profile。
- [x] 运行后端完整测试；新外部依赖在既有测试中显式模拟，不实际请求网络。

### 3. 页面和交付验证

- [x] 新增公司/行业范围筛选、行业状态、每条关联解释和晨报关联展示；保留既有情绪卡片。
- [x] 更新 README 的范围、额度、错误状态和验证边界。
- [x] npm run build；git diff --check；运行后端完整测试。
- [x] 使用隔离 SQLite 和模拟 API 验证页面；真实只验证不收费的行业资料获取。
- [x] 根据 requesting-code-review 技能进行独立审查，解决重要问题并重新验证。

## Review Focus

- 源站返回另一只股票或无效行业：拒绝并保留已验证缓存。
- 新行业无映射：仍构建通用查询，不绑定默认股票列表。
- 行业关键词仅在侧栏/标题：需正文事件依据，不能凭查询词保留。
- 同篇文章跨公司：采集共享，研判独立；晨报显示各股票关联、不套用另一只股票评分。
- 行业接口部分失败：保留旧行业材料并明确状态，公司材料照常更新。

## Execution ledger

Ruling: 在用户指定的当前目录内增量实现并保留全部已有改动；使用 .runtime/industry-news-baseline 保存涉及文件的原始快照供核对，避免切换另一个工作区丢失并行功能上下文。

Task 1: complete — 9 initial service/rule tests passed.
Task 2: complete — final 22 industry tests and full 147-test suite passed.
Task 3: complete — npm run build passed; scope/sentiment/new-stock UI checks passed with explicit simulation labels; no browser console errors; 6 real industry profiles retrieved.
Ruling: reviewer reproduced company-stage blocking, loss of per-company stale labels and a pre-existing news_index filter exception. Added failing tests, fixed each and re-ran the full suite.
Ruling: source classification for 601857 is 炼化及贸易; expanded sector aliases instead of hardcoding stock identity.
Ruling: paid news/model integration and live deployment remain unverified because this local checkout has no keys; implementation and simulated integration are complete.

Final review: no remaining Important/Critical findings; reviewer independently ran all 22 industry tests. Cancellation retains existing company/industry news, and manual analyses save the same prompt-version marker as automatic analyses.
Final verification: 147 tests passed in 18.019s; npm run build passed; browser verified scope/sentiment/new-stock views without console errors. No commits, pushes, production deployment or paid provider calls.

Follow-up authorization: 用户要求审查市场情绪和社区卡片、Git 提交推送，并通过 SSH 拉取部署。补充数据校验、失败退避、非阻塞行情、独立轮询和旧记录兼容后执行完整验证。

Follow-up verification: 154 tests passed in 11.884s; final production build and git diff --check passed. Independent reviewer verified market field semantics from official source and ran 18 market/bundle tests; no remaining Important/Critical findings.

Deployment finding: Linux tests passed (154 cases), but the individual quote profile host rejects server requests. Added identity-validated Eastmoney F10 classification fallback; 3 new provider tests passed, then full local suite passed (157 cases). Independent review accepted the fallback; added malformed-row validation from the minor review note.
