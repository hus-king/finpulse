"""Single-bond research rules for one point-in-time closing snapshot."""

import math
from datetime import date
from statistics import median


PEER_VALUE_RANGE = 10
PEER_TERM_DAYS = 365
MIN_PEERS = 5
SHOCK = 0.10
ENTRY_FRACTION = 0.30
SWITCH_BUFFER = 0.01
DOWNSIDE_LIMIT = 0.01
EPSILON = 1e-12
EXIT_REASONS = {
    'call': '已公告实施强赎',
    'credit': '已披露明确兑付风险',
    'exit': '已知退出事件',
}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _days_remaining(row, as_of):
    try:
        return (date.fromisoformat(row['maturity_date']) - as_of).days
    except (KeyError, TypeError, ValueError):
        return None


def _entry_issues(row, days_remaining, order_size):
    issues = []
    risk = row.get('risk')
    if risk != 'clear':
        issues.append(row.get('risk_reason') or EXIT_REASONS.get(risk, '条款或信用状态待核验'))
    if row.get('scenario_call_sensitive') is True:
        issues.append('强赎可能改变涨跌情景的退出路径')
    if not row.get('tradable'):
        issues.append('当前不可交易')
    if any(not _finite(row.get(key)) or row[key] <= 0 for key in ('price', 'conversion_value')):
        issues.append('缺少有效债价或转股价值')
    if not row.get('rating') or days_remaining is None or days_remaining <= 0:
        issues.append('缺少有效债项评级或剩余期限')
    turnover = row.get('turnover_20d')
    if not _finite(turnover) or turnover <= 0:
        issues.append('缺少近 20 日成交额')
    elif order_size > turnover * 0.01:
        issues.append('拟投入金额超过近 20 日成交额中位数的 1%')
    return issues


def _relative_return(row):
    stock = row.get('stock_return_20d')
    industry = row.get('industry_return_20d')
    if _finite(stock) and stock > -1 and _finite(industry) and industry > -1:
        return (1 + stock) / (1 + industry) - 1
    return None


def _estimate(peers, target):
    matches = [p for p in peers if abs(p['conversion_value'] - target) <= PEER_VALUE_RANGE + EPSILON]
    if len(matches) < MIN_PEERS:
        return None, None, matches
    premium = median(p['premium'] for p in matches)
    return target * (1 + premium), premium, matches


def evaluate_rows(as_of, rows, position_code=None, order_size=200_000):
    """Value each bond, retain review evidence, then rank only valid candidates."""
    if not _finite(order_size) or order_size <= 0:
        raise ValueError('拟投入金额必须为正数')
    day = date.fromisoformat(as_of)
    prepared = []
    for row in rows:
        remaining = _days_remaining(row, day)
        prepared.append((row, remaining, _entry_issues(row, remaining, order_size)))
    usable_peers = [
        (row['rating'], remaining, {
            'code': row['code'], 'name': row['name'], 'conversion_value': row['conversion_value'],
            'premium': row['price'] / row['conversion_value'] - 1,
        })
        for row, remaining, issues in prepared if not issues
    ]
    scored = []
    for row, remaining, issues in prepared:
        entry = {
            **row, 'reference_price': None, 'discount': None, 'relative_return': _relative_return(row),
            'upside': None, 'downside': None, 'peer_count': 0, 'peer_premium': None, 'peers': [],
            'up_reference': None, 'down_reference': None, 'rank': None,
            'reasons': issues, 'eligible': False,
        }
        scored.append(entry)
        if issues:
            continue
        peers = [
            peer for rating, days, peer in usable_peers
            if peer['code'] != row['code'] and rating == row['rating'] and abs(days - remaining) <= PEER_TERM_DAYS
        ]
        value, price = row['conversion_value'], row['price']
        reference, premium, matches = _estimate(peers, value)
        entry.update(reference_price=reference, peer_premium=premium, peer_count=len(matches),
                     peers=sorted(matches, key=lambda p: p['code']))
        if reference is None:
            issues.append('当前转股价值附近不足 5 只可比券')
            continue
        entry['discount'] = (reference - price) / price
        up, _, _ = _estimate(peers, value * (1 + SHOCK))
        down, _, _ = _estimate(peers, value * (1 - SHOCK))
        entry.update(up_reference=up, down_reference=down)
        if up is None or down is None:
            issues.append('正股涨跌 10% 情景下的可比券不足')
        elif up + EPSILON < reference or down > reference + EPSILON:
            issues.append('情景参考价与股价方向不一致，暂无法评估')
        else:
            entry.update(upside=(up - reference) / price, downside=(down - reference) / price)
        if entry['relative_return'] is None:
            issues.append('缺少正股或同行业近 20 日收益')
        elif entry['relative_return'] <= EPSILON:
            issues.append('正股近 20 日未跑赢行业')

    valued = sorted((row for row in scored if row['discount'] is not None),
                    key=lambda row: (-row['discount'], row['code']))
    cutoff = math.ceil(len(valued) * ENTRY_FRACTION)
    candidates = []
    for place, entry in enumerate(valued):
        if entry['discount'] <= EPSILON:
            entry['reasons'].append('当前债价未低于同类券参考价')
        elif place >= cutoff:
            entry['reasons'].append('低估幅度未进入估值可用券的前 30%')
        if not entry['reasons']:
            candidates.append(entry)
    candidates.sort(key=lambda row: (-row['upside'], -row['downside'], -row['discount'], row['code']))
    for rank, entry in enumerate(candidates, start=1):
        entry.update(rank=rank, eligible=True)
    return sorted(scored, key=lambda row: (row['rank'] or math.inf, row['code'] != position_code, row['code']))


def decide(entries, position_code=None, previous_thesis_failures=0, switch_cost=0.004):
    """Propose a holding action. Execution and confirmed holdings are owned by the caller."""
    if not _finite(switch_cost) or switch_cost < 0:
        raise ValueError('换仓成本必须为非负数')
    candidate = next((row for row in entries if row.get('rank') == 1 and row.get('eligible')), None)
    current = next((row for row in entries if row['code'] == position_code), None)
    summary = {
        'candidate_code': candidate['code'] if candidate else None,
        'thesis_failures': 0, 'upside_edge': None, 'downside_deterioration': None,
        'switch_cost': switch_cost, 'switch_threshold': switch_cost + SWITCH_BUFFER,
        'downside_limit': DOWNSIDE_LIMIT,
    }

    def result(action, reason):
        return {**summary, 'action': action, 'reason': reason}

    if not position_code:
        if candidate:
            return result('enter', '当前空仓，首选券通过新建仓条件')
        if not any(all(row.get(key) is not None for key in ('discount', 'relative_return', 'upside', 'downside'))
                   for row in entries):
            return result('unavailable', '缺少可完整评估的转债数据，等待更新')
        return result('cash', '本周没有同时满足低估、正股和交易条件的候选')

    if current is None:
        return result('unavailable', '本期快照缺少原券，保留持仓并核验状态')
    risk = current.get('risk')
    if risk in EXIT_REASONS:
        return result('exit', current.get('risk_reason') or EXIT_REASONS[risk])
    if risk != 'clear':
        return result('unavailable', '原券条款或信用状态待核验，保留持仓')

    metrics = (current.get('discount'), current.get('relative_return'))
    thesis_failed = any(_finite(metric) and metric <= EPSILON for metric in metrics)
    if thesis_failed:
        summary['thesis_failures'] = min(previous_thesis_failures + 1, 2)
    if candidate and candidate['code'] != position_code:
        if all(_finite(row.get('upside')) for row in (candidate, current)):
            summary['upside_edge'] = candidate['upside'] - current['upside']
        if all(_finite(row.get('downside')) for row in (candidate, current)):
            summary['downside_deterioration'] = current['downside'] - candidate['downside']

    if not current.get('tradable'):
        return result('hold', '原券当前不可交易，保留持仓')
    if thesis_failed:
        if summary['thesis_failures'] < 2:
            return result('hold', '原券本周已无折价或正股未跑赢行业，等待下次复核')
        if candidate and candidate['code'] != position_code:
            return result('switch', '原券连续两周失去低估或正股驱动，首选券通过新建仓条件')
        return result('hold', '原券连续两周失去低估或正股驱动，暂无合格替代')
    if not all(_finite(metric) for metric in metrics):
        return result('unavailable', '原券估值或正股收益暂无法评估，保留持仓')
    if not candidate:
        return result('hold', '本周暂无合格替代，继续持有原券')
    if candidate['code'] == position_code:
        return result('hold', '原券仍是本周首选')
    if summary['upside_edge'] is None or summary['downside_deterioration'] is None:
        return result('unavailable', '原券涨跌情景暂无法评估，保留持仓')
    if summary['upside_edge'] <= summary['switch_threshold'] + EPSILON:
        return result('hold', '首选券上行情景优势未超过换仓成本和缓冲')
    if summary['downside_deterioration'] > summary['downside_limit'] + EPSILON:
        return result('hold', '首选券下行情景损失增幅超过 1 个百分点')
    return result('switch', '首选券通过上行优势与下行损失两道换仓门槛')
