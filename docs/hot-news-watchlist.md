# 重要热点新闻与自选股

当前筛选标准、检索实例与反例见[新闻事件筛选标准](hot-news-selection.md)。入口为主导航「热点榜单」或`/?view=hot-news`。

## 候选与筛选

候选来自百度、微博全领域热榜，以及配置后启用的Tushare兼容资讯接口。热榜最多50条/平台，保留真实名次；资讯查询近24小时的财联社、新浪、华尔街见闻快讯与新闻通讯，每源至多100行，优先具体事件候选及较新报道，至多15条进入后续筛选。`major_news`并不保证都是重大新闻，仍需筛选。

先判断明确主体、具体新变化与重要性，再判断经济传导；覆盖政策、国际冲突、供应中断、产品安全、重要新品、重大公司变化和技术落地。计划内发布可以入选。泛趋势、宣传、消费现象、个人体验、小事、无新增量的旧闻、例行报价不入选，不以能联想到行业为充分条件。

模型每批最多5条、最多并行2批，返回筛选判断、事件类别、主体、动作、新事实引文、新增性、重要性、报道归属、经济路径和至多3个板块。逐字检查主体、新事实及板块引文，检查ID覆盖和枚举。投诉/测评以归属方式记录，不视为缺陷已确认。没有配置或模型失败时使用保守规则，无法确认重要新事件时不发布。

每项需要`event.novelty=new_development`，`importance`为`major_public/major_industry/major_brand`之一。缓存绑定提示词与版本，旧版本不沿用旧标准发布；格式与引文校验不能替代语义评估及事实核验。

## 用户页面

只显示最终保留的新闻，不再提供待核实、已排除或全部话题标签。未选候选仍在后端审计记录中，公共GET和更新响应都不返回它们。

每张新闻卡依次展示新闻、板块及自己的相关自选股。公共新闻无需登录；个人判断需要登录，默认批量读取已保存结果，主动点击才生成。新闻接口没有热度或原文URL时，显示报道时间与来源入口，不伪装热搜名次或文章原文链接。正文用于后端分析，公共列表不返回内部完整正文。

可按关键词、来源、经济影响类型过滤，选择综合热度/时效或最近更新排序，以及20/50/100条展示。平台名次融合不相加原始热度；资讯没有平台排名，按报道时间补充。列表中的优先位置不表示涨跌幅或影响大小。

## 更新与失败处理

- `POST /api/hot-news/refresh`：登录、同源、CSRF，每账号15分钟至多8次；启动共享后台任务，运行中返回202，已有新快照可直接返回200。
- `GET /api/hot-news`：公开只读，返回保留新闻、来源状态及`refresh`进度，不触发采集/模型。页面运行中每2秒读取一次；网络失败后仍能继续查询，离开页面不取消服务器任务。
- `refresh.status`为`idle/running/completed/error`；运行阶段包括采集和筛选，并带处理数量。重复更新加入已有任务；跨进程租约防重，重启中断与失败可重试。
- 候选和筛选每10分钟复用；源失败沿用可用旧材料至多一小时，保留原时间并标旧。所有来源失败不伪造数据或制造示例新闻。
- 资讯服务超时、权限到期、缺字段、异常日期会明确处理；不把错误返回正文或token透传给用户。
- 所有API响应为`Cache-Control: no-store`。数据复用`research_records`命名空间，无数据库结构迁移，无新自动调度。

公开筛选参数：`source=all/baidu/weibo/tushare_cls/tushare_sina/tushare_wallstreetcn/tushare_major`；`channel=all/trade/supply/cost/demand/regulation/funding/risk`；`q`至多80字符；`sort=hot/latest`；`limit=1..100`。旧客户端即使传`decision=all/review/exclude`也只收到保留新闻。`counts`只含当前匹配的`keep`，`candidate_total`只统计可发布新闻。

## 个股分析

`GET /api/hot-news/analysis-batch?ids=...`批量读取当前账号已保存判断；`GET /{id}/analysis`读取明细；`POST /{id}/analyze`主动核对主营资料与新闻，均在`/api/hot-news`前缀下。

资讯正文存在时用于关联分析，平台话题仍使用标题/摘要；两者都不冒充独立核实的报道。逐股判断直接/间接/无关联/待核实，利好/利空/正反并存/影响不明，并保存新闻与主营原文、条件和资料时间。板块假设不能覆盖逐股业务角色，不以股价涨跌验证关联。详情见[个股设计](hot-stock-architecture.md)。

## 运行配置与验证

服务端支持`config.local.json`中的`tushare_news`对象，或该文件真实路径同目录的`news-provider.local.json`，均含`base_url`和`token`。独立密钥文件权限600、Git忽略；从SDK对应的REST协议直接读取，不新增运行依赖。主配置和已购买的兼容服务地址由运维私有保存，不下发前端。

回归命令：

```bash
.venv/bin/python -m unittest tests.test_hot_news_events tests.test_tushare_news tests.test_hot_news_board tests.test_hot_news tests.test_hot_stock_impact tests.test_hot_sectors -q
npm run build
```

浏览器脚本`tests/hot_news_ui.mjs`覆盖只显示保留新闻、后台更新及只读进度轮询、来源失败、资讯无虚构名次、组合筛选、个股证据与重试、账号私有读取、深浅色和手机布局。测试资料明确标注情景，不冒充生产新闻。

早期版本的部署记录见[2026-10-26发布报告](superpowers/reports/2026-10-26-hot-news-release.md)。此前的价格实验仅归档，不作为当前功能验收目标。
