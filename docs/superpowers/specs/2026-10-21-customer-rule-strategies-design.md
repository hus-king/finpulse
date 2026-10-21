# 客户 JSON 规则策略接入设计

用户已要求新增网页端客户算法提交接口与统一规范，并选择“上传 JSON 规则策略包，由 FinPulse 自动运行”。沿用现有 Strategy 契约、账号鉴权、模拟盘执行器和每日调度；在当前会话实施、验证、合并main并同步线上。

## 统一策略包

固定 `schema_version: finpulse.rules.v1`，字段为 name（1–60字符）、description（最多1000）、version（1–32字符，字母数字点横线）、universe（1–30个去重沪深A股代码）、score（有界算术表达式）、filters（至多12个比较条件，全部满足）、selection（top_n 1–5，min_score有限数或null）、allocation（单票0–20%、总仓0–80%）。不接受owner、路径、URL、Python代码、动态依赖或自定义调度。

表达式只允许 `{feature: name}`、`{value: finite_number}`、`{op: add|sub|mul|div|min|max|abs, args:[expression,...]}`。只能引用当前12个ohlcv-v1特征；每条完整规则合计最多128个表达式节点、深度6、常数绝对值最多1000000。比较为gt/gte/lt/lte/eq；除零或非有限计算导致整个决策失败并停止本轮，不解释为空仓。拒绝未知字段、布尔数值、NaN/Infinity、超大或超深输入。

策略的 daily cadence 固定为现有交易日北京时间10点之后，每日一个账户决策，数据仅取上一完成交易日。评分排序与筛选、最多5只和目标权重由规则适配器产生，最终资金和风险规则仍由共同执行器校验。JSON解释器不使用eval/exec、不运行用户Python、不访问网络或文件。

## 账号与版本

使用research_records的user_strategy命名空间，key为服务器分配的user-UUID，owner来自登录会话。每账号最多10份策略（每包最多64KiB）；操作在现有用户行锁下完成。保存规范化包、SHA256和递增revision。更新和删除须提交expected_revision；正在托管的策略必须先暂停才能改/删。策略版本绑定revision和摘要；已有持久化决策仍按原模型版本恢复，不能被新规则替换。

官方目录继续使用StrategyRegistry；增加owner-aware resolver，只能解析官方或当前账号的策略。返回origin=official|user，前端按来源区分，防止用户名称冒充官方标识。不改动其他用户和现有官方策略。

## HTTP 接口

- GET /api/paper/strategy-schema：公开规范、字段限制、特征说明、示例和JSON Schema；不包含用户数据。
- POST /api/paper/strategies/validate：登录+CSRF，纯本地校验，返回规范化JSON、SHA256与摘要，不取行情、不下单。
- POST /api/paper/strategies：登录+CSRF，body `{strategy: package}`，创建本人的策略。
- GET /api/paper/strategies：返回官方策略和本人策略目录，其他账号不可见。
- GET /api/paper/strategies/{id}：只读本人提交包，未知/他人均404。
- PUT /api/paper/strategies/{id}：body `{expected_revision, strategy}`，版本校验更新。
- DELETE /api/paper/strategies/{id}：body `{expected_revision}`，仅删除本人的未托管策略；不会删除历史成交/决策。

所有用户数据响应no-store，写入沿用Origin/CSRF和停用用户校验。策略提交路由的真实请求体在Pydantic解析前限制64KiB；新建/校验/编辑/删除合计每账号每分钟20次，超出429。语法校验不触发外部数据请求。启用后的行情仍共享现有按股票/交易日缓存，不进行新闻检索和收费模型调用。

## 网页

模拟盘新增“我的策略”编辑器，提供示例、JSON文件导入、规则校验、创建、选择编辑、导出与删除；展示校验错误及服务器规范化结果。文件大小先在浏览器检查，后端仍独立校验。启用由现有操作方式下拉框选择“我的策略”，与官方策略明确区分，支持同样的暂停、持仓导航和决策记录。

用户策略展示规则、版本和未回测提示，不显示官方模型的研究报告或把用户策略标为官方。编辑器的加载与校验不修改操作方式；当前策略托管中时只允许查看/导出，先暂停才能更新或删除。返回其他页面及刷新后由服务器恢复本人配置。

## 验证与发布

测试真实解释器计算、非法表达式/深度/节点/有限数、稳定摘要、相同分数排序、除零失败关闭；接口鉴权/CSRF/账号隔离/大小/配额/CAS/停用用户/托管期间修改拒绝；用户策略通过共同执行器落单和重启幂等，官方回归；浏览器创建/导入/校验/选择/启用/暂停/编辑/删除与手机布局。运行完整Python、Node和前端构建后独立审查，合并main并发布同版本，保留数据库、私有配置、已启用用户策略和回退快照。
