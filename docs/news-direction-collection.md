# 新闻方向模型的历史补采

脚本 [`scripts/backfill_direction_data.py`](../scripts/backfill_direction_data.py) 在部署服务器的现有 Python 环境中运行，读取已配置的 Tavily 搜索和腾讯日线接口，将结果保存为独立训练语料。它不写线上数据库、不启动分析模型或发送通知，不更改自动调度。

## 2026-10-22 批次范围

按用户选择，补采现有合格新闻对应的23只股票，新闻为2026-09-22至2026-10-21的30个自然日，日K线为2025-10-22至2026-10-21的365个自然日。停牌、未上市及休市不保证有日线。

用户随后要求停止采集并训练，实际冻结62/115个新闻时间片、28/46次日线请求，尚有53个新闻时间片和18次日线请求未完成。冻结后未再发起采集请求。新语料含568个合格正文版本（新增96个，对应94篇新增独立正文）、9386根不复权日线（新增1260根）和7238根前复权参考日线。`collection-report.json` 的 `complete: false` 表示原计划未采满；本次组装没有处理失败。第二轮训练见[实验记录](experiments/2026-10-22-company-attention/README.md)。

新闻按7日切片，每片使用公司名、代码及“财经新闻 公司公告”检索，最多20条，5片共115次逻辑搜索。使用已配置账户的 Tavily advanced 搜索，会消耗搜索额度；账户切换或重试可能产生额外 HTTP 请求。每个股票任务依次获取日线和新闻，并发2个股票任务。

历史补采结果不是完整的新闻档案。返回数量达到20的时间片记为 `saturated_windows`；结果不足20也不能证明覆盖完整。缺少全文或正文日期未核验的文章不能进入当前训练器。原始响应、清洗拒绝原因、失败列表及行情修订均保留。

## 运行与续跑

部署服务器代码不一定包含离线实验模块。将 `scripts/backfill_direction_data.py` 和 `backend/news_direction.py` 放在同一个独立采集目录，`--root` 指向当前发布入口，以复用线上数据适配器和配置。不要覆盖发布目录文件。

```bash
/home/airhust/finpulse-deploy/shared/.venv/bin/python \
  /home/airhust/finpulse-deploy/shared/training-data/backfill_direction_data.py \
  --root /home/airhust/finpulse-deploy/current \
  --baseline /home/airhust/finpulse-deploy/shared/training-data/ssh-collection-20261021/corpus.json \
  --output /home/airhust/finpulse-deploy/shared/training-data/history-collection-20261022 \
  --end 2026-10-21 --news-days 30 --market-days 365
```

相同命令可续跑：成功响应立即原子保存，已有请求结果直接复用，失败请求在下次运行重试。输出目录中的 `plan.json` 必须与请求计划相同，不能用不同股票、日期或基线混写同一批次。`--max-search-requests` 默认300，启动前检查逻辑搜索数量。只采少量股票可用 `--codes 600519,300750`。

停止正在运行的采集进程后，在相同命令后加 `--assemble-only` 可只用已保存响应组装快照；缺失请求计入报告，不发起网络抓取。

产物包括：

- `news/`：每只股票每个区间的原始搜索响应、查询区间和实际获取时间。
- `market/`：分别保存不复权和前复权日线响应。
- `corpus.json`：旧语料与新增新闻、日线合并后的新语料，旧文件不改写。
- `market-revisions.json`：本次覆盖的旧日线及新值，便于检查源修订或单位差异。
- `collection-report.json`：请求、正文合格数、拒绝原因、失败及语料指纹。

`complete: true` 表示计划的请求和处理没有记录失败，不表示搜索覆盖了所有历史新闻。

## 时间口径

新闻保留 `publication_date` 和 `first_seen_at`。补抓正文的可获得时间使用实际响应保存时间；相同内容已有更早的可核验观察时保留旧时间，不根据文章发布日期倒填。

补历史新闻可以扩大 `publication_replay` 的探索资料，但不能制造过去真实运行时已经见过这些文章的证据，也不能直接延长严格 `observed` 测试期。严格测试还需要后续真实观察日及至少3个交易日的标签等待期。

不复权日线用于当前涨跌标签，前复权日线用于除权变化检查。前复权数据是本次获取的参考快照，不是历史时点可获得的复权数据库；成交量沿用部署 AkShare 适配器的单位约定，修订记录应在重新训练前审查。

新语料不能直接配用旧 `embeddings.npz`。重新训练前必须对新增合格正文生成相匹配的向量并重新检查指纹。本次采集不会改变已有模型权重或评估成绩。

## 检查

```bash
python3 -m unittest tests.test_direction_backfill tests.test_news_direction -v
```

新增5项检查覆盖切片连续性、真实采集时间、片段不能充当全文、区间外正文不能入库，以及续跑不重复调用接口、保留旧观察时间、拒绝改写已有计划。5项均在部署服务器的现有虚拟环境中通过；本机缺少 `httpx` 时跳过依赖线上适配器的续跑用例。
