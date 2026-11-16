"""Build the stock-bond research snapshot from dated index, yield and ETF data."""
from argparse import ArgumentParser
from collections import deque
import csv
from datetime import date
import json
from math import isfinite
from pathlib import Path
from statistics import mean, median, quantiles
import tempfile


ROOT = Path(__file__).resolve().parents[1]
ARCHIVES = ROOT / 'public/market-archives'
LONG = {'511010': '国泰上证5年期国债ETF'}
BASKETS = {
    'long': LONG,
    'three': {**LONG, '511260': '国泰上证10年期国债ETF', '511020': '平安中证5-10年期国债活跃券ETF'},
}
HORIZONS = (20, 60, 120)
FLOW_WINDOW = 20
FLOW_HISTORY = 252
YIELD_HISTORY = 756
ENTRY_LAG = 2
GROUPS = {
    'baseline': '全部可分组月末',
    'low_strong': '低利率＋强申购',
    'high_weak': '高利率＋弱申购',
}
METHOD = (
    '20 日净申赎率为连续 20 个交易日的份额变动金额之和除以期初规模，'
    '日金额按当日单位净值估算。'
    '利率参照此前 756 个有效交易日，申赎率参照此前 252 个有效交易日，各取上下 20% 分位。'
    '强申购要求净流入，弱申购要求净流出。月末观察，信号后第 2 个交易日开盘起算，'
    '持有 20／60／120 个交易日，以沪深 300 价格指数计算。较长收益窗口有重叠。'
)


def normal_date(value):
    return date.fromisoformat(value.strip()).isoformat()


def number(value):
    if value is None or not value.strip():
        return None
    result = float(value)
    if not isfinite(result):
        raise ValueError(f'非有限数值 {value!r}')
    return result


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as source:
        return list(csv.DictReader(source))


def aggregate_flows(sessions, fund_data, codes=LONG):
    """Estimate net subscriptions from adjacent official share observations."""
    history = {}
    window = deque(maxlen=FLOW_WINDOW)
    for prev_day, day in zip(sessions, sessions[1:]):
        net, assets = 0., 0.
        valid = True
        for code in codes:
            previous = fund_data.get((prev_day, code))
            current = fund_data.get((day, code))
            if not previous or not current:
                valid = False
                break
            previous_shares, previous_nav, _ = previous
            shares, nav, split_ratio = current
            if previous_shares is None or not previous_nav or shares is None or not nav or not split_ratio:
                valid = False
                break
            net += (shares - previous_shares * split_ratio) * nav
            assets += previous_shares * previous_nav
        if not valid or assets <= 0:
            window.clear()
            continue
        window.append((net, assets))
        if len(window) == FLOW_WINDOW:
            history[day] = sum(amount for amount, _ in window)*100/window[0][1]
    return history


def group_months(sessions, yields, flows):
    rates = deque(maxlen=YIELD_HISTORY)
    subscriptions = deque(maxlen=FLOW_HISTORY)
    groups = {key: [] for key in GROUPS}
    for i, day in enumerate(sessions):
        rate = yields.get(day)
        flow = flows.get(day)
        month_end = i+1 == len(sessions) or day[:7] != sessions[i+1][:7]
        if (month_end and rate is not None and flow is not None
                and len(rates) == YIELD_HISTORY and len(subscriptions) == FLOW_HISTORY):
            groups['baseline'].append(i)
            rate_low, _, _, rate_high = quantiles(rates,n=5,method='inclusive')
            flow_low, _, _, flow_high = quantiles(subscriptions,n=5,method='inclusive')
            if rate <= rate_low and flow > 0 and flow >= flow_high:
                groups['low_strong'].append(i)
            if rate >= rate_high and flow < 0 and flow <= flow_low:
                groups['high_weak'].append(i)
        if rate is not None:
            rates.append(rate)
        if flow is not None:
            subscriptions.append(flow)
    return groups


def summarize(index, entries):
    horizons = []
    for days in HORIZONS:
        returns = []
        for i in entries:
            entry = i + ENTRY_LAG
            exit_ = entry + days - 1
            if exit_ < len(index):
                returns.append(100*(index[exit_][1]/index[entry][0] - 1))
        horizons.append(dict(days=days, n=len(returns),
                             mean_pct=round(mean(returns),4) if returns else None,
                             median_pct=round(median(returns),4) if returns else None,
                             positive_pct=round(100*sum(value>0 for value in returns)/len(returns),2)
                             if returns else None))
    return horizons


def build(index_source, yield_source, etf_source, basket='long'):
    funds = BASKETS[basket]
    index = {}
    for row in csv_rows(index_source):
        day = normal_date(row['date'])
        opening, closing, multiple = (number(row[key]) for key in ('index_open','index_close','pe_ttm'))
        if day in index or not (opening and closing and opening>0 and closing>0):
            raise ValueError(f'指数数据无效或重复 {day}')
        index[day] = opening, closing, multiple
    yields = {}
    for row in csv_rows(yield_source):
        day = normal_date(row['date'])
        if day in yields:
            raise ValueError(f'国债收益率日期重复 {day}')
        yields[day] = number(row['cn_gov10_pct'])

    fund_data = {}
    for row in csv_rows(etf_source):
        if row['code'] not in funds:
            continue
        key = (normal_date(row['date']),row['code'])
        shares, nav, ratio = (number(row[column]) for column in ('shares','nav','split_ratio'))
        if key in fund_data or any(value is not None and value<=0 for value in (shares,nav,ratio)):
            raise ValueError(f'ETF 数据无效或重复 {key}')
        fund_data[key] = shares, nav, ratio

    sessions = sorted(index)
    flows = aggregate_flows(sessions, fund_data, funds)
    groups = group_months(sessions, yields, flows)
    points = []
    for day in sessions:
        _, closing, pe = index[day]
        rate = yields.get(day)
        spread = 100/pe-rate if rate is not None and pe and pe>0 else None
        flow = flows.get(day)
        points.append(dict(date=day, index_close=closing, cn_gov10_pct=rate,
                           spread_pp=round(spread,4) if spread is not None else None,
                           flow_20d_pct=round(flow,4) if flow is not None else None))
    values = [index[day] for day in sessions]
    return dict(as_of=sessions[-1], flow_first=min(flows,default=None), flow_last=max(flows,default=None),
                funds=[dict(code=code,name=name) for code,name in funds.items()], method=METHOD,
                history=points,
                groups=[dict(id=key,label=label,horizons=summarize(values,groups[key]))
                        for key,label in GROUPS.items()])


def main():
    parser = ArgumentParser(description=__doc__)
    for name in ('index','yield','etf'):
        parser.add_argument('--'+name,dest=name+'_source',required=True,type=Path)
    parser.add_argument('--basket',choices=BASKETS,default='long')
    parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    snapshot = build(args.index_source,args.yield_source,args.etf_source,args.basket)
    if not snapshot['flow_first']:
        parser.error('ETF 数据没有连续 21 个交易日的有效观测')
    output = args.output or ARCHIVES/('stock-bond.json' if args.basket == 'long' else 'stock-bond-three.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=output.parent,delete=False) as stream:
        json.dump(snapshot,stream,ensure_ascii=False,separators=(',',':'),allow_nan=False)
        stream.write('\n')
    temporary = Path(stream.name)
    temporary.chmod(0o644)
    temporary.replace(output)
    print(snapshot['flow_first'],snapshot['flow_last'],snapshot['groups'])


if __name__=='__main__':
    main()
