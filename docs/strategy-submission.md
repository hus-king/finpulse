# 客户策略提交规范：finpulse.rules.v1

在模拟盘的“我的策略 · 导入与管理”中使用示例或导入 JSON，校验并保存。然后在“操作方式 → 我的策略”选择并启用。默认仍是手动；保存和校验不触发交易。已启用策略必须先暂停，才能修改或删除。每个账号的策略只对本人可见，官方算法与本人策略分组显示。

这是服务端解释的 JSON 规则，不是 Python/JavaScript 插件。没有 eval、模块导入、网络访问或文件读写能力。首版不提供任意代码执行、实时回调、客户密钥或外部 webhook。

完整机器规范可公开读取 `GET /api/paper/strategy-schema`，包含 `json_schema`、`example`、指标说明、限制与执行频率。网页提供示例和 JSON Schema 下载；仓库中同一份[示例](../examples/strategies/momentum-v1.json)和 [JSON Schema](../examples/strategies/finpulse-rules-v1.schema.json)可直接交给客户开发工具使用。JSON Schema 中的字段约束之外，服务端还检查总节点、深度、股票板块、运算参数个数和限额；服务端校验结果为准。

## 策略包

```json
{
  "schema_version": "finpulse.rules.v1",
  "name": "我的20日动量",
  "description": "只用于说明提交格式，尚未回测",
  "version": "1.0.0",
  "universe": ["000001", "600519"],
  "score": {"feature": "return_20"},
  "filters": [
    {"left": {"feature": "volatility_20"}, "op": "lt", "right": {"value": 0.05}}
  ],
  "selection": {"top_n": 2, "min_score": 0},
  "allocation": {"per_stock_weight": 0.2, "max_total_weight": 0.4}
}
```

字段不接受未知扩展。`name` 为1–60字符；`description` 最多1000字符；`version` 为1–32字符，以字母或数字开头，后续仅字母、数字、`.`、`_`、`-`。`universe` 为1–30个不重复的沪深A股六位代码，提交阶段验证代码格式与板块，执行前再核验交易所股票目录。

`score` 与筛选条件两侧均可使用三类表达式：

| 表达式 | 语义 |
| --- | --- |
| `{"feature":"return_20"}` | 引用一个已有指标 |
| `{"value":0.6}` | 有限数常量，绝对值不超过1,000,000 |
| `{"op":"mul","args":[{"feature":"return_20"},{"value":0.6}]}` | 对子表达式运算 |

支持 `add`、`mul`、`min`、`max`（2–8参数），`sub`、`div`（2参数），`abs`（1参数）。全部评分与筛选表达式合计最多128个节点，每条表达式最多6层。评分不接受字符串、布尔或非有限数；JSON不允许重复字段、NaN、Infinity。

`filters` 最多12项，比较符 `gt/gte/lt/lte/eq` 分别表示大于/大于等于/小于/小于等于/等于，全部条件必须满足。按评分降序选 `top_n`（1–5）只；同分按股票代码升序。评分必须 **大于等于** `min_score`；设为 `null` 表示不限制评分。默认 `top_n=5,min_score=0,filters=[]`。

`allocation` 权重为0–1的小数，单票最多0.2、总仓最多0.8，默认分别0.2/0.8。入选数为N时，每只目标权重为 `min(per_stock_weight,max_total_weight/N)`。未入选且在该策略股票池内的持仓目标为0；池外持仓保留，并计入全账户风控。因此修改股票池不会自动卖出移出股票池的持仓，需要手动处理。

## 指标及执行语义

特征版本 `ohlcv-v1`，仅使用上一完整交易日及之前的65根未复权日线；交易日历未知、日线缺失、停牌、OHLC异常或公司行为参照不一致时等待，不以缺失数据产生清仓信号。

| 指标 | 定义 |
| --- | --- |
| return_1/5/10/20/60 | 最近对应交易日数的收盘收益率 |
| ma_5/20/60 | 最新收盘价 / 对应日均收盘价 − 1 |
| volatility_20 | 最近20个单日收益率的总体标准差 |
| volume_5/20 | 最新成交量 / 对应日均成交量 − 1 |
| range_20 | 最近20日 `(high-low)/close` 的均值 |

服务器每分钟检查，交易日北京时间10:00后，按账户配置版本执行当日一次决策；关闭网页仍继续运行。报价、T+1、交易时段、板块数量、手续费、现金、租约、订单UUID去重、暂停锁与官方算法共用。总持仓最多5只、单票20%、总仓80%、每日成交额30%，相对托管历史峰值回撤15%时暂停；上述上限不能保证实际回撤或市场变动后的权重始终在范围内。

除零、溢出等运行错误会使整个本轮决策失败，保留既有持仓并5分钟后重试。空的正常选股结果则代表现金目标，可以卖出池内可卖持仓。提交校验只验证格式与边界，**不是回测，不验证收益，也不保证运行中不会出现除零**。网页示例尚未回测。

## HTTP 接口

除公开schema外均需登录cookie，写入必须同源并携带该会话的 `X-CSRF-Token`；沿用 `/api/auth/login` 或 `/api/auth/register` 返回的 `csrf_token`。不支持第三方跨域直接操作账户。

| 方法与路径 | 请求 / 返回 |
| --- | --- |
| GET /api/paper/strategy-schema | 公开规范、示例、指标、限额 |
| GET /api/paper/strategies | 官方及当前用户策略元数据，`origin=official\|user` |
| POST /api/paper/strategies/validate | `{strategy:包}` → `valid,strategy,digest,summary`，不保存 |
| POST /api/paper/strategies | `{strategy:包}` → 201，保存为新策略 |
| GET /api/paper/strategies/{id} | 本人记录 `id,revision,digest,strategy,created_at,updated_at` |
| PUT /api/paper/strategies/{id} | `{strategy:包,expected_revision:当前策略revision}` → 更新后的记录 |
| DELETE /api/paper/strategies/{id} | `{expected_revision:当前策略revision}` → `{deleted:true,id}` |
| GET /api/paper/automation | 当前模式、配置revision、调度开关及状态 |
| PUT /api/paper/automation | `{mode,strategy_id,expected_revision,策略为用户时strategy_revision}` |
| GET /api/paper/automation/decisions | 决策审计，支持 `before,limit` 分页 |

策略最多10份/账号；校验、创建、修改和删除合计每分钟20次/账号；整个提交HTTP请求（含外层字段）最多65,536字节。客户端应为外层字段预留空间。请求JSON嵌套最多32层，规则深度另受6层限制。API均拒绝访问其他用户的策略ID。编辑/删除/启用共享同一用户行锁；失效版本不得启用或覆盖。

`version` 为客户声明版本，可自由递增；`revision` 为服务端每次保存递增的整数。执行版本为 `声明版本:r修订号:SHA256前12位`，完整 `digest` 对规范化JSON计算。启用用户策略必须提交当前 `strategy_revision` 和配置 `expected_revision`，两种版本分别检查。改名或重复保存也会增加策略revision。删除策略保留已成交及决策记录。

常见错误：401未登录；403 CSRF/停用；404 `STRATEGY_NOT_FOUND`；409 `STRATEGY_REVISION_CONFLICT`、`STRATEGY_IN_USE`、`STRATEGY_QUOTA`、`AUTOMATION_CONFLICT`；413 `STRATEGY_TOO_LARGE`；422规则/JSON格式不符合规范或缺少 `strategy_revision`；429 `STRATEGY_RATE_LIMIT`。校验字段错误在 `detail` 数组中，业务错误在 `detail` 字符串和 `code` 中。

Python调用示例（`requests`，不执行客户代码）：

```python
import getpass
import requests

base = 'https://your-finpulse.example'
session = requests.Session()
auth = session.post(base + '/api/auth/login', headers={'Origin': base}, json={
    'username': input('用户名: '), 'password': getpass.getpass(),
})
auth.raise_for_status()
headers = {'Origin': base, 'X-CSRF-Token': auth.json()['csrf_token']}

import json
with open('momentum-v1.json', encoding='utf-8') as source:
    package = json.load(source)
checked = session.post(base + '/api/paper/strategies/validate', headers=headers,
                       json={'strategy': package})
checked.raise_for_status()
created = session.post(base + '/api/paper/strategies', headers=headers,
                       json={'strategy': package})
created.raise_for_status()
record = created.json()
state = session.get(base + '/api/paper/automation')
state.raise_for_status()
# 明确启用后才开始自动管理；只保存则不要调用下面的PUT。
enabled = session.put(base + '/api/paper/automation', headers=headers, json={
    'mode': 'algorithm', 'strategy_id': record['id'],
    'strategy_revision': record['revision'], 'expected_revision': state.json()['revision'],
})
enabled.raise_for_status()
```

## 扩展接口与负载

可信官方算法仍使用 `StrategyRegistry.register()`；客户规则通过 `RuleStrategy` 实现同一个 `metadata/validate_config/decide` 接口，统一交给执行器生成订单。后续可增加新的有版本JSON规则适配器，不需要向客户开放服务器代码执行权限。

校验与保存只做CPU计算和账号存储，不调用付费分析模型、搜索或行情接口。启用后复用按股票/信号日共享的日线缓存和分钟行情限流；新增股票池会增加首次行情请求（每只约两份日线数据）。每账号股票池上限不能代替全站日配额，当前无全站硬日请求上限；网页管理面板增加一次schema读取，保留原有30秒状态轮询。
