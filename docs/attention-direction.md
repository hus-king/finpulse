# 公司背景、新闻和时间注意力：离线训练原型

本实现使用冻结的 Qwen3 新闻及公司向量，训练小型公司交叉注意力、每日新闻注意力、GRU和时间注意力。首轮真实训练结果见 [实验记录](experiments/2026-10-21-company-attention/README.md)。新结构没有在本轮超过原GRU，当前不能据此声称注意力或公司背景带来预测增益。

2026-10-22使用停止补采时的568个合格正文版本完成[第二轮训练](experiments/2026-10-22-company-attention/README.md)：严格小测试原GRU为27/37，完整注意力模型25/37；更长回放未超过多数类基线。两轮均保留独立产物。

## 实际网络

```text
新闻向量1024 → 共享可训练投影32维 + 新闻年龄/时间精度编码
公司主营原文卡1024 → 同一投影32维
新闻作为query，公司卡作为key/value → 单头交叉注意力 → 门控残差融合
每天最多32篇新闻 + 可学习空新闻 → 注意力加权汇总
每日新闻32维 + 每日量价6维 + 日期/缺失信息4维 → 单层32维GRU
全部10个GRU状态 → 时间注意力 → 加权状态32维
加权状态 + 最后状态 + 截止日量价10维 + 公司资料可用标记
→ 16维ReLU/Dropout(0.2) → 未来第3个交易日涨跌logit
```

完整模型有49,363个可训练参数；冻结向量保存为模型buffer，不参与梯度更新。新闻、公司、时间三个注意力层均为小型模块，没有训练或微调Qwen权重，也没有接入网页或交易调度。

公司卡有明确的空资料选项，每日新闻聚合也有空新闻选项；没有新闻、没有公司背景及全部padding时仍能正常运行。注意力输出仅表示内部选择权重，不是因果归因，也不是经过校准的置信度。

公司资料目前只有实际缓存的主营概述及逐字业务片段。没有制造财务比例、产业链、成本敞口或其他关系标签；公司对同一新闻的不同反应需要依靠训练数据学习，结构本身不会自动给出可靠的经济传导知识。

## 时间与数据隔离

新闻继续使用现有正文核验、版本可获得性、转载去重及10个交易日窗口。公司卡从公共`company_business`缓存及公共分析记录的输入背景中提取，导出器不导出生成分析、分数、账号或凭据。

公司版本的`known_at`使用能够确认该完整版本已存在的保存时间，所有口径都必须满足`known_at <= 预测截止时刻`。当前公司事实是在预测截止时可用的背景，用来重新理解整个历史新闻窗口；不宣称这些事实在窗口内每一个更早日期也已知。

每日量价特征只使用相应日收盘前的日线。量价10维及每日6维的标准化均只从训练样本计算。模型不使用股票ID嵌入，不输入AI评分或事后AI摘要。公司、新闻的padding在注意力层中掩码；未到可获得时间的公司卡不进入输入。

训练与验证、验证与测试之间清除跨边界的3日标签。测试价格、标签及特征不能影响训练权重或标准化参数；相关用例在实际训练环境通过。

## 本轮固定方案

训练前保存`plan.json`，记录语料/向量/源码指纹、候选、种子与超参数。对照包含量价逻辑回归、新闻逻辑回归、原GRU、不输入公司卡的分层注意力GRU、完整公司注意力GRU。

原GRU与新模型的比较同时改变了新闻聚合、每日量价输入、时间特征及注意力读取方式，不能单独归因于某一注意力层。两个新模型共享结构与超参数，消融版本屏蔽公司卡和公司可用标记，用于检查增加公司资料后的差异。

下游训练使用CPU、种子42；注意力模型AdamW学习率0.003、weight decay 0.01、batch 64、梯度裁剪1。正常划分最多200轮，以验证logit BCE早停，patience 25；候选按验证平衡准确率、再按对数损失选择，之后才计算测试成绩。

严格时间数据不足三段划分时，固定30轮，预先指定完整模型为演示候选；不会根据测试成绩改选或调参。`publication_replay`允许后来获取的新闻正文回放到发布日期，仅作探索；公司背景仍遵守实际可获得时间，因此较早训练期可能完全没有公司资料。

## 复现

依赖为现有`requirements-direction.txt`，不修改Web服务Python环境。以下命令从项目根目录执行；hustcs已有解释器为`/home/hustcs/miniconda3/envs/py10/bin/python`。

1. 在可访问部署数据库的环境中只读导出公司资料：

```bash
python -m scripts.export_direction_company_facts --root /path/to/deployed/checkout > company-facts.json
```

2. 冻结同一Qwen版本编码主营原文卡：

```bash
python -m scripts.embed_company_facts \
  --facts .runtime/news-direction/attention-20261021/company-facts.json \
  --model /home/hustcs/.local/share/finpulse-qwen-probe-20261020/model \
  --output .runtime/news-direction/attention-20261021/company
```

3. 使用本轮语料、对应新闻向量和公司卡向量训练；输出目录必须不存在，以免覆盖已查看过的实验：

```bash
python -m scripts.train_attention_direction train \
  --corpus .runtime/news-direction/ssh-collection-20261021/corpus.json \
  --news-embeddings .runtime/news-direction/ssh-collection-20261021/embeddings.npz \
  --company-dir .runtime/news-direction/attention-20261021/company \
  --output .runtime/news-direction/attention-20261021/run-01
```

4. 用保存的完整模型再次预测；预测始终按实际可获得时间取新闻和公司资料：

```bash
python -m scripts.train_attention_direction predict \
  --corpus .runtime/news-direction/ssh-collection-20261021/corpus.json \
  --news-embeddings .runtime/news-direction/ssh-collection-20261021/embeddings.npz \
  --company-dir .runtime/news-direction/attention-20261021/company \
  --artifacts .runtime/news-direction/attention-20261021/run-01/observed \
  --output .runtime/news-direction/attention-20261021/run-01/latest_predictions.json
```

当前预测器针对训练时的冻结向量ID集合及公司卡快照校验并重载，提供本轮保存/加载验证；尚未实现面向新文章向量集合的线上增量推理。

## 检查

```bash
python -m unittest discover -s tests -p '*direction*.py' -v
```

训练环境中26项检查通过，其中9项为本次新增，覆盖未来公司版本过滤、原文卡来源、逐日行情截止、无新闻/无公司数值稳定、日内新闻顺序不变性、公司消融、padding隔离、测试集不影响训练和权重往返加载。10份模型权重已重新加载，全部逐样本预测与保存CSV完全一致；被篡改的权重文件被拒绝。另保存21只股票的研究预测。

训练权重、原始公司资料、向量和完整运行日志保存在忽略目录`.runtime/news-direction/attention-20261021/`，同路径同步到hustcs实验目录。公开实验记录仅包含模型指标与逐样本评估结果，不提交正文或二进制权重。
