# 图表技术指标计算口径

`src/technicalIndicators.ts` 是纯计算模块，接受按时间升序排列的原始 `Candle` OHLCV，输出与输入等长的数列，不修改输入。图表即使显示 Heikin Ashi，也应传入变换前的原始行情。参数里的“周期”表示一根当前图表K线；周线指标应先对原始日K做周聚合，再计算。

这些是公开公式的明确实现，**没有承诺与东方财富或其他终端逐值一致**。终端可能使用不同默认参数、复权数据、历史长度、平滑种子、取整、缺值与零分母规则。本模块仅使用传入的公开OHLCV，不包含真实Level-2、委托队列或逐笔订单数据；DKX等名称也不表示获取到了主动买卖逐笔数据。

## 记号、种子与缺值

- `C,H,L,O,V` 分别为收盘、最高、最低、开盘、原始成交量，`REF(X,k)` 为k柱前的X。成交量沿用上游单位，不除以100或10000；只有LON公式中的100是公式自身的常数。
- `MA(X,n)`、`SUM(X,n)`、`HHV/LLV(X,n)` 都要求当前柱在内的连续n个有效值。历史不足或窗口中缺值时输出 `null`，不补零、不缩短窗口。
- `EMA(X,n)` 权重为 `2/(n+1)`，以第一个有效X直接作为种子，即第一柱就是有效的递归估计；不人为等到第n柱。这与已有 `chartMath.ts` 的EMA定义一致。
- `S(X,n)` 表示权重为 `1/n` 的递归平均。KDJ/LWR在第一个完整RSV窗口之前输出 `null`，第一个有效RSV按先验50更新（K、D分别有50先验）；LON则以第一个有效LONG直接播种。先验不是用于伪造历史K线的零填充。
- `W(X,n)` 为Wilder平均：先等待n个连续有效输入，取其简单均值播种，再使用 `1/n` 权重更新。RSI与已有 `chartMath.ts` 的Wilder定义一致。
- 平滑遇到缺值时输出 `null` 并重新播种；窗口遇到缺值时，直到其移出窗口才恢复。累计类OBV/LON保留此前累计值，但缺少当前必需字段的柱输出 `null`，不会插值补出丢失的成交量。
- 所有输出只允许有限数值或 `null`。默认除数为0时输出 `null`（包括0/0）；下表列出的中性约定除外。非有限原始字段按缺值处理，溢出也转为 `null`。
- `partial: true` 表示该柱仍未完整；使用已经观察到的OHLCV正常计算，保持partial状态由调用者显示。此时最后一柱指标会随行情变化；缺少的历史周期仍是 `null`。

## 公式与默认参数

表中“首值索引”为从0开始的最早可计算位置，假设字段有效且除数非零；中性处理也可能产生有效的0。信号线在其自身输入具备完整周期后开始。

| 指标 | 默认参数与精确公式 | 首值索引 |
| --- | --- | --- |
| VOL | `VOL=V`；`MA5=MA(V,5)`；`MA10=MA(V,10)` | 0；4；9 |
| MACD | `DIF=EMA(C,12)-EMA(C,26)`；`DEA=EMA(DIF,9)`；柱 `MACD=2*(DIF-DEA)` | 全部0 |
| KDJ | `RSV=100*(C-LLV(L,9))/(HHV(H,9)-LLV(L,9))`；`K=S(RSV,3)`；`D=S(K,3)`；`J=3K-2D` | 全部8 |
| RSI | 对n=6/12/24：`delta=C-REF(C,1)`；`G=W(MAX(delta,0),n)`；`D=W(MAX(-delta,0),n)`；`RSIn=100*G/(G+D)` | 6；12；24 |
| BIAS | 对n=6/12/24：`BIASn=100*(C-MA(C,n))/MA(C,n)` | 5；11；23 |
| CCI | `TP=(H+L+C)/3`；`A=MA(TP,14)`；`MD=本14柱TP相对于当前A的平均绝对偏差`；`CCI=(TP-A)/(0.015*MD)` | 13 |
| WR | 对n=10/6：`WRn=100*(HHV(H,n)-C)/(HHV(H,n)-LLV(L,n))`，采用正值0–100表示法 | 9；5 |
| ATR | `TR=max(H-L,abs(H-REF(C,1)),abs(L-REF(C,1)))`；首柱 `TR=H-L`；`ATR=W(TR,14)` | 13 |
| BOLL | `MID=MA(C,20)`；`sigma=sqrt(20柱(C-MID)^2之和/20)`；`UPPER=MID+2*sigma`；`LOWER=MID-2*sigma`，使用总体标准差 | 全部19 |
| EXPMA | `EMA12=EMA(C,12)`；`EMA50=EMA(C,50)` | 全部0 |
| ENE | `UPPER=MA(C,10)*1.11`；`LOWER=MA(C,10)*0.91`；`ENE=(UPPER+LOWER)/2` | 全部9 |
| OBV | 第一柱基线0；后续 `OBV=前OBV+sign(delta)*V`；平盘量不计；`MAOBV=MA(OBV,30)` | 0；29 |
| BBI | `(MA(C,3)+MA(C,6)+MA(C,12)+MA(C,24))/4` | 23 |
| BRAR | `AR=100*SUM(H-O,26)/SUM(O-L,26)`；`BR=100*SUM(MAX(H-REF(C,1),0),26)/SUM(MAX(REF(C,1)-L,0),26)` | 25；26 |
| TRIX | `T=EMA(EMA(EMA(C,12),12),12)`；`TRIX=100*(T-REF(T,1))/REF(T,1)`；`MATRIX=MA(TRIX,20)` | 1；20 |
| VR | 在26柱内，U为上涨柱成交量和，D为下跌柱成交量和，F为平盘柱成交量和；`VR=100*(U+F/2)/(D+F/2)`；`MAVR=MA(VR,6)`；首柱无前收盘，不计入完整变化窗口 | 26；31 |
| DMI | `up=H-REF(H,1)`，`down=REF(L,1)-L`；`+DM=up>0且up>down时取up，否则0`；`-DM=down>0且down>up时取down，否则0`；相等时两者均0；`PDI=100*SUM(+DM,14)/SUM(TR,14)`，MDI同理；`DX=100*abs(PDI-MDI)/(PDI+MDI)`；`ADX=MA(DX,6)`；`ADXR=(ADX+REF(ADX,6))/2`。TR从第二柱起参与完整比较窗口。 | PDI/MDI 14；ADX 19；ADXR 25 |
| DPO | 采用非居中、仅使用历史数据的21周期变体：`DPO=C-REF(MA(C,21),11)`；`MADPO=MA(DPO,6)` | 31；36 |
| DMA | `DIF=MA(C,10)-MA(C,50)`；`AMA=MA(DIF,10)` | 49；58 |
| CR | 前中价 `PM=REF((H+L)/2,1)`；`CR=100*SUM(MAX(H-PM,0),26)/SUM(MAX(PM-L,0),26)`；`MA5/10/20=MA(CR,n)`，均线不位移 | CR 26；均线30/35/45 |
| LWR | `R=100*(HHV(H,9)-C)/(HHV(H,9)-LLV(L,9))`；`LWR1=S(R,3)`；`LWR2=S(LWR1,3)`，两层初始先验50 | 全部8 |
| SKDJ | `R=100*(C-LLV(L,9))/(HHV(H,9)-LLV(L,9))`；`K=EMA(EMA(R,3),3)`；`D=MA(K,3)` | 8；10 |
| DKX | `MID=(3C+L+O+H)/6`；连续最近20柱线性加权，`DKX=SUM((20-k)*REF(MID,k),k=0..19)/210`；`MADKX=MA(DKX,10)` | 19；28 |
| MTM | `MTM=C-REF(C,12)`；`MAMTM=MA(MTM,6)` | 12；17 |
| LON | `q=(V+REF(V,1))*(C-REF(C,1))/(100*(HHV(H,2)-LLV(L,2)))`；`LONG=自输入历史开始的q累计和`；`LON=S(LONG,10)-S(LONG,20)`；`LONMA=MA(LON,10)` | 1；10 |

## 零分母与变体说明

- KDJ、WR、LWR、SKDJ的最高最低差为0时，原始位置取50（中性）。KDJ的J可越出0–100，因此返回值不设置固定范围、不截断J。
- RSI无跌幅但有涨幅为100，无涨幅但有跌幅为0，两者都为0取50。CCI的平均绝对偏差为0时取0。
- DMI真实波幅和为0时，PDI与MDI都取0；两者之和为0时DX取0。ATR的0是已经观测到真实波幅为0的有效值。
- BIAS、BRAR、CR、VR、TRIX分母为0时保留 `null`；不输出无穷大，也不任意封顶。因而单边上涨且没有下跌/平盘量时，VR可以一直不可计算。
- LON双柱高低差为0时增量取0；其第一根可用柱的LONG同时作为两条递归平均的种子，因此LON首值为0。这是有意义的播种结果。累计和从当前加载历史开始，不代表从上市首日开始，因此改变加载历史可能改变LON、OBV及所有递归指标的后续值。
- DMI采用14柱滚动方向运动与真实波幅和、6柱简单ADX的公开常见口径；它不同于14/14全Wilder平滑的DMI。CR选前一柱高低均价、无位移均线，区别于使用HLC典型价及位移均线的变体。
- DPO选择21柱均值与11柱滞后；其他终端常用20柱均值。这里不做向左回填或使用未来K线。
- DKX明确选择连续20柱线性加权。部分公开公式（包括TqSdk实现）最后一项写成 `REF(MID,20)`，跳过19柱滞后；本项目采用连续20柱版本，因此该类终端不能逐值对照。
- LON选择东方财富公开介绍中的“双柱量/双柱高低差累积LONG，再取权重1递归平均差”变体；没有使用另一个常见的TB/TS量能分配再动态平均变体。公开介绍未固定平均线N，这里明确选择10。**这是一种公开公式实现，不是东方财富逐值复刻，也不是L2指标。**

## 公开来源与项目约定

以下优先引用终端提供者的帮助文档或开发者源码。来源用于核对公式体系；本文件给出的参数、播种、完整窗口、零分母和变体选择是本项目的最终计算约定。

- [Futu API技术指标参数说明](https://openapi.futunn.com/futu-api-doc/hk/quote/get-stock-filter.html)：公开MACD(12,26,9)、KDJ(9,3,3)、BOLL(20,2)参数。
- [TqSdk官方技术指标API](https://doc.shinnytech.com/tqsdk/latest/reference/tqsdk.ta.html)及[维护者公开源码ta.py](https://github.com/shinnytech/tqsdk-python/blob/master/tqsdk/ta.py)：参考MA/EMA、BIAS、WR、BRAR、BBI、EXPMA、DMA、DPO、MTM等公式体系，以及DMI/DKX/CR/LON存在的不同变体。没有复制其代码或使用其依赖。
- [TradingView官方CCI说明](https://cn.tradingview.com/support/solutions/43000502001/)：典型价均值、平均绝对偏差及0.015常数；本项目默认14柱。
- [Moomoo官方BOLL说明](https://www.moomoo.com/ca/hans/support/topic3_241)：均值上下两倍标准差；总体标准差及20柱窗口与现有 `chartMath.ts` 保持一致。
- [Moomoo官方VR说明](https://www.moomoo.com/mm/hans/support/topic3_815)：上涨、下跌和平盘成交量的比值。
- [Moomoo官方CR说明](https://www.moomoo.com/mm/hans/support/topic3_248)：使用前一柱高低均价的强弱比以及5/10/20柱均线。
- [东方财富LWR介绍](https://qhweb.eastmoney.com/videos/10/1952702.html)与[SKDJ介绍](https://qhweb.eastmoney.com/videos/10/1977610.html)：LWR(9,3,3)正值双曲线与SKDJ(9,3)的公开定义。[同花顺公式平台公开SKDJ公式](https://poi.10jqka.com.cn/store/formula/detail/indexid/109801)展示两层EMA及MA信号线；该链接为公式发布者的原始公式，不表示平台认证或逐值一致。
- [东方财富DKX介绍](https://qhweb.eastmoney.com/videos/10/2017291.html)：OHLC加权基准价与越近权重越高的公开原理；本项目的具体连续20柱权重见上。
- [东方财富LON介绍](https://qhweb.eastmoney.com/videos/10/1998214.html)：本项目选择的双柱量价累计LONG变体来源。
- [信易科技TRIX公开实现说明](https://www.shinnytech.com/articles/trading-strategy/trend-following/trix-strategy)：三层EMA与百分比变化率。
- [同花顺公式平台ENE说明](https://poi.10jqka.com.cn/store/formula/detail/indexid/23039)：上下轨道与中轨的公开定义；本项目采用10/11/9参数。
- 项目现有 `src/chartMath.ts`：EMA首价播种、总体BOLL标准差、RSI完整变化窗口后Wilder递归的既有约定。

输入价格若不复权，拆股、分红、除权等价格跳变会进入指标计算。这里不进行企业行动校正、不推断复权因子，也不把观测中的partial柱视为已经收盘。
