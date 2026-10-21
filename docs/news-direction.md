# 新闻与量价预测未来3个交易日：离线实验

这版实现历史材料导出、冻结 Qwen3 新闻向量、样本构建、训练、时间留出测试、模型保存和再次预测。运行在独立实验目录，未接入网页、模拟盘自动交易或线上服务调度。

第一轮实验日期：2026-10-21。实际结果见 [实验记录](experiments/2026-10-21-news-direction/README.md) 和该目录的 `report.json`。当前样本不足以证明新闻有稳定预测增益，不应将输出分数解释成可信的上涨概率。

## 模型与目标

- 冻结本地 `Qwen3-Embedding-0.6B`，每个新闻版本产生1024维向量。输入包含该材料中的公司名、代码、标题和清洗正文；不使用新闻研判分数、事后AI总结或当前公司简介。
- 同一股票、同一天的新闻向量取均值，保留10个交易日序列和新闻数量／缺失标记；同URL只使用当时可获得的最新版本，相同正文的转载去重，每日最多32篇。
- 数值特征取21根完整日线，包含过去1／5／10／20日收益、均线偏离、波动率、量比和日内振幅。标准化参数只从训练集计算。
- 四个对照模型：量价逻辑回归、新闻逻辑回归、新闻＋量价MLP、新闻＋量价GRU。GRU先将每日新闻输入降至32维，使用单层32维GRU，再与数值特征融合。
- 每个样本是“股票×预测日期”，信息截止北京时间15:00。标签为第3个后续市场交易日收盘价相对当日收盘价的正／负变化，零收益不归为跌。

日线来自已保存的行情，不调用新闻检索或付费大模型。共享市场日历由所有已存日线日期组成；缺失或停牌日不会被误算成“下一个交易日”。当日15:00前导出的未完成日线不参与构样本。

目标明确为**未复权收盘价方向**，不是总收益或可成交策略收益。有复权参照的窗口使用OHLC仿射一致性检查，排除跨除权／除息的窗口；没有参照的样本单独计数。没有补抓复权数据，因此第一轮并非所有样本都完成这项检查。

## 两种时间口径

`observed` 同时要求正文核验的发布日期和采集快照时间不晚于预测截止时刻。`first_seen_at` 是同一材料版本在现存快照中最早能确认的保存时刻；取数据库最后更新时间与快照采集时间的较晚者，避免覆盖后的正文被回填到旧时刻。它是保守的可获得时间，不能还原已经丢失的更早版本。

`publication_replay` 只按发布日期放入历史窗口，允许使用后来才采到的正文。该口径只作探索回放，不能称为严格时间回测。两种口径均只接受 `extracted_body + body_verified`；日期只有年月日时，保守记为当日23:59:59，最早在下个交易日收盘样本中出现。这个过滤不等于独立核实了新闻来源的真实性。

正常情况下，全部股票共同按日期60%／20%／20%分为训练、验证、测试；训练标签结束日期必须早于验证开始日，验证标签结束日期必须早于测试开始日。模型按验证集平衡准确率、再按验证损失选择，测试集不参与早停或选择。

如果日期不足以完成上述划分，但仍有至少6个预测日期，则只做一个**固定参数流程演示**：最后两个日期作为测试，其余样本清除与测试区间重叠的标签后用于训练；固定30轮，不留验证集，不早停，预先指定GRU作为默认演示模型。不能依据这个极小测试集挑选模型。数据再少、训练集缺少类别或完全没有新闻时，写出 `insufficient_data`，不会改用随机划分。

## 复现

Web服务和实验的依赖分别管理。已有GPU环境包含这些依赖，无需修改线上Python环境。其他机器可以在独立虚拟环境安装 `requirements-direction.txt`；PyTorch的CUDA版本应与机器环境匹配。

### 1. 只读导出已有数据

在可访问研究数据库的项目目录运行：

```bash
python -m scripts.export_direction_data --output .runtime/news-direction/corpus.json
```

导出器只查询 `collection`、`daily_market`、`quant_history` 的共享记录，不初始化数据库，不导出账号、会话、用户设置、模型密钥、AI研判内容或模拟盘记录。`quant_history` 在当前线上版本提供现成复权参照；旧版本没有该命名空间时结果为空。

也可以将脚本通过SSH标准输入交给服务器已有Python运行，并用 `--root` 指定部署目录。数据库配置在服务器内读取，不复制到实验机器。导出和模型文件放在被Git忽略的 `.runtime/` 内。

### 2. 缓存新闻向量

```bash
python -m scripts.embed_direction_news \
  --corpus .runtime/news-direction/corpus.json \
  --model /path/to/Qwen3-Embedding-0.6B \
  --output .runtime/news-direction/embeddings.npz
```

默认最多1024 token、batch=2、左侧padding、末个有效token池化并L2归一化。长正文会截断，截断数量记录在元数据中。不会更新embedding模型。缓存记录正文ID、语料SHA256、模型权重SHA256及编码设置；语料改变后需重新编码。

### 3. 训练并测试

```bash
python -m scripts.train_news_direction train \
  --corpus .runtime/news-direction/corpus.json \
  --embeddings .runtime/news-direction/embeddings.npz \
  --output .runtime/news-direction/run-20261021
```

输出总报告，以及每个可运行口径的4份模型、`manifest.json` 和逐样本 `test_predictions.csv`。全量候选在测试前固定，随机种子为42；正常划分最多训练200轮、验证BCE连续25轮无改善时停止。不会根据第一轮测试结果反复调参。

### 4. 用保存的模型预测

```bash
python -m scripts.train_news_direction predict \
  --corpus .runtime/news-direction/corpus.json \
  --embeddings .runtime/news-direction/embeddings.npz \
  --artifacts .runtime/news-direction/run-20261021/observed \
  --output .runtime/news-direction/run-20261021/latest_predictions.json
```

预测始终使用 `observed` 的新闻可获得时间，即使加载的是回放口径模型。默认加载该次实验的默认模型，也可用 `--model fusion_gru` 等选择已训练候选作研究检查。输出明确区分模型是否使用新闻、可用新闻数量、模型选择方式和未校准分数；所有结果均为 `research_only`，不进入交易模块。

输入最新导出和对应新向量，可以复用已有训练权重；embedding权重和编码设置必须与训练时相同。预测会校验模型文件SHA256，并以 `weights_only=True` 加载权重。

## 第一轮运行位置

- GPU服务器：SSH连接所用的 `hustcs@222.20.95.156:40068`。
- 独立目录：`/home/hustcs/.local/share/finpulse-news-direction-20261021`。
- 已有解释器：`/home/hustcs/miniconda3/envs/py10/bin/python`。
- 已有embedding模型：`/home/hustcs/.local/share/finpulse-qwen-probe-20261020/model`。
- 语料、向量、权重和预测结果位于该独立目录的 `.runtime/news-direction/`；本机同样保留一份。

从独立目录执行上述命令；向量计算使用GPU，下游小模型使用CPU。没有修改线上发布目录、服务配置或自动交易状态。

## 验证

```bash
python -m unittest discover -s tests -p test_news_direction.py -v
```

覆盖采集时间、日期精度、盘后新闻、未完成日线、正文版本、转载去重、停牌、零收益、除权参照、时间划分、标签区间清除，以及测试数据不能影响训练权重／标准化参数、保存后预测和权重完整性。未安装训练依赖的机器会跳过训练相关用例。

数据来自近期已关注股票，存在股票池选择偏差，且历史行情可能修订。冻结的预训练编码器也不能替代真正前瞻的评估。后续应先积累覆盖更长、连续保存正文版本和采集时刻的历史，再固定方案做新的时间留出测试；当前不能从准确率推出交易收益。
