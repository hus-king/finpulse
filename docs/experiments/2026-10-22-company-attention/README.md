# 2026-10-22：停止补采后的第二轮训练

用户要求先停止当前采集并再训练一轮。部署端采集进程已终止，只用成功落盘的响应组装新快照，没有继续请求缺失区间。训练与编码在 hustcs 的既有离线实验环境完成，旧语料、向量和首轮权重保留。

本轮原GRU在严格时间小测试中为27/37（72.97%），全预测跌基线为26/37（70.27%），完整公司注意力模型为25/37（67.57%）。历史回放的验证集选择原GRU，其测试准确率62.03%，低于多数类62.66%。没有证明增加注意力、公司背景或这批数据带来稳定收益。

## 冻结数据

- 原计划23只股票、30天新闻和365天日线；停止时已保存62/115个新闻切片和28/46次日线请求。余下53个新闻切片和18次日线请求未完成，本轮不再续抓。
- 611条搜索候选；新增96个合格正文版本，新增独立正文94篇。合格版本总数568，独立正文484篇。新增正文发布日期为2026-09-22至2026-10-21，真实采集时间在2026-10-22北京时间。
- 不复权日线9386根（新增1260），前复权参考7238根（新增2464）。616条重叠日线的变化全部为前复权参考的成交量，重叠OHLC未改变；新增参考覆盖使更多除权窗口被剔除。
- 语料SHA256：`351d8cbf41fb86a3ea7fb8c493ba66f5757913df063f227827b040b3057abcc6`。
- 同一冻结Qwen3-Embedding-0.6B重新编码全部568个正文，向量1024维、最大1024 token，348篇截断；GPU编码耗时42.81秒。公司资料沿用上一轮有真实可获得时间的84张原文卡。

详情见 [collection-report.json](collection-report.json)、[coverage-report.json](coverage-report.json) 和 [freeze.json](freeze.json)。`complete: false` 指采集原计划未采满，本次冻结处理没有失败。

## 结构与训练方案

结构沿用[上一轮实现](../../attention-direction.md)，未根据本轮测试结果改网络或调参。对照为量价逻辑回归、新闻逻辑回归、原融合GRU、新闻/时间注意力GRU、公司/新闻/时间注意力GRU。

完整注意力模型49,363个可训练参数，原GRU39,905个。Qwen保持冻结；下游分类器在CPU训练，seed 42、4线程。注意力模型AdamW、学习率0.003、weight decay 0.01、batch 64、dropout 0.2、梯度裁剪1；正常划分最多200轮，验证logit BCE早停，patience 25。训练前保存 [plan.json](plan.json)。

严格时间没有验证集，所有模型固定30轮，预先指定完整公司注意力模型为演示候选，不按测试成绩重新选择权重。回放按验证平衡准确率、再按log loss选择，选择完成后计算测试指标。原GRU在回放第8轮取得保存状态，两个注意力模型保存第1轮状态。

## 严格时间：只有两个测试日期

新闻使用 `max(发布时间, 实际观察时间)`，公司卡在两种时间口径下都只能使用截止时刻已知的版本。新增正文在10月22日获取，不能倒填为过去已知的新闻，因此没有制造新的严格观察日期。

训练40条、2个日期（10月8—9日），其中20条有新闻、4条同时有新闻与公司资料；清除60条跨边界样本。测试37条、2个日期（10月15—16日），33条有新闻和公司资料，标签最晚到10月21日。

| 模型 | 准确率 | 平衡准确率 | MCC | Brier |
|---|---:|---:|---:|---:|
| 全预测跌/训练多数类 | 70.27% | 50.00% | 0.000 | 0.2117 |
| 量价逻辑回归 | 48.65% | 52.97% | 0.055 | 0.2773 |
| 新闻逻辑回归 | 62.16% | 52.10% | 0.045 | 0.2160 |
| 原融合GRU | 72.97% | 57.17% | 0.240 | 0.2028 |
| 新闻/时间注意力GRU | 64.86% | 51.40% | 0.035 | 0.2702 |
| 公司/新闻/时间注意力GRU | 67.57% | 53.32% | 0.089 | 0.2701 |

原GRU仅比全预测跌多答对1条。37条股票日也只有2个独立日期，不支持未来稳定准确率的判断。

旧轮75%来自30/40。本轮因新增除权参考剔除4条旧测试样本，并补入1条此前缺日线的样本，因此不能直接解释成从75%下降到72.97%。在共同的36条、标签相同的样本中，旧GRU答对27条，本轮26条；也没有显示提升。该比较仍使用已看过的日期，不是新独立测试。见 [paired-previous-test.json](paired-previous-test.json)。

## 按发布日期历史回放：探索结果

回放允许后来获取的正文按发布日期进入过去的窗口，不能当作当时线上可获得的数据。公司卡仍遵守真实获取时间，不把当前公司资料回放到历史。

| 分区 | 样本 | 日期数 | 日期范围 | 有新闻 | 有公司 |
|---|---:|---:|---|---:|---:|
| 训练 | 1478 | 88 | 03-03—07-09 | 29 | 0 |
| 验证 | 492 | 28 | 07-15—08-21 | 11 | 0 |
| 测试 | 640 | 31 | 08-27—10-16 | 297 | 90 |

另清除100条跨边界样本。新采新闻主要落在较近的测试期，不能为了扩大训练量而将这些文章移到更早的训练日期。本轮训练期有新闻的样本仍少，训练期完全没有可用公司背景，因此不能据此评价公司注意力的泛化能力。

| 模型 | 准确率 | 平衡准确率 | MCC | Brier |
|---|---:|---:|---:|---:|
| 全预测跌/训练多数类 | 62.66% | 50.00% | 0.000 | 0.2345 |
| 量价逻辑回归 | 46.56% | 51.52% | 0.032 | 0.2568 |
| 新闻逻辑回归 | 54.06% | 55.31% | 0.103 | 0.2467 |
| 原融合GRU（验证集选出） | 62.03% | 49.59% | -0.042 | 0.2407 |
| 新闻/时间注意力GRU | 62.66% | 50.00% | 0.000 | 0.2350 |
| 公司/新闻/时间注意力GRU | 62.66% | 50.00% | 0.000 | 0.2353 |

两个注意力模型均全预测跌，其准确率完全等于多数类。即使某些概率指标不同，也没有显示方向分类增益。全部指标、按日期/新闻/公司子集结果见 [report.json](report.json)，逐条概率见 [observed-test-predictions.csv](observed-test-predictions.csv) 与 [publication_replay-test-predictions.csv](publication_replay-test-predictions.csv)。

## 验证与产物

31项数据、模型及采集检查在训练环境通过。10份权重全部重新加载，逐条预测与保存CSV最大差异0；损坏的权重被拒绝加载。另保存19只满足当前行情/除权过滤条件的股票预测，仍按真实可获得时间取输入。详情见 [verification.json](verification.json)。

本机与GPU实验根目录保留：

```text
.runtime/news-direction/history-collection-20261022/
  corpus.json, embeddings.npz, embedding-metadata.json
  collection-report.json, coverage-report.json, freeze.json
  news/, market/, market-revisions.json
.runtime/news-direction/attention-20261022/
  company/, company-facts.json
  embedding.log, tests.log, train.log, verification.log
  run-01/plan.json, report.json, verification.json, paired-previous-test.json
  run-01/observed/{5份模型权重, manifest.json, test_predictions.csv}
  run-01/publication_replay/{5份模型权重, manifest.json, test_predictions.csv}
  run-01/latest_predictions.json
```

GPU实验根目录为 `/home/hustcs/.local/share/finpulse-news-direction-20261021`。训练没有改线上发布目录、常驻API或交易策略，权重仍为离线研究用途。当前预测器绑定本轮语料、向量ID集合与公司卡快照，尚不是增量新闻线上推理服务。

复现命令：

```bash
python -m scripts.train_attention_direction train \
  --corpus .runtime/news-direction/history-collection-20261022/corpus.json \
  --news-embeddings .runtime/news-direction/history-collection-20261022/embeddings.npz \
  --company-dir .runtime/news-direction/attention-20261022/company \
  --output /path/to/new-empty-run-directory
```
