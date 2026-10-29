"""Frozen PEG B holding rules. Prices here are CNY; ledger money remains fen."""
import hashlib
import json
import math
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from ..trading_calendar import is_trade_day
from .peg_strategy import PegStrategy

BUFFERED_PEG_ID = 'official-peg-buffered-v1'
BUFFERED_VERSION = 'peg-b-3x9-stop12-band5-v1'
_INDUSTRY_BYTES = Path(__file__).with_name('sw_industries.json').read_bytes()
INDUSTRIES = json.loads(_INDUSTRY_BYTES)
INDUSTRY_VERSION = hashlib.sha256(_INDUSTRY_BYTES).hexdigest()[:16]


def industry_at(code, day):
    day = day.replace('-', '')
    matches = [r for r in INDUSTRIES['rows'].get(code, [])
               if r['start'] <= day and (not r['end'] or day <= r['end'])]
    return max(matches, key=lambda r:r['start'])['name'] if matches else None


class BufferedPegStrategy:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot

    def metadata(self):
        return {'id':BUFFERED_PEG_ID, 'name':'PEG B · 3股分散与免微调', 'version':BUFFERED_VERSION,
                'available':True, 'kind':'peg', 'cadence':'交易日10:00决策，10:02卖出、10:07买入',
                'universe':[], 'description':'固定3股、前9保留、一级行业分散、12%价格退出、10交易日冷却、5个百分点免微调。'}

    def validate_config(self, config):
        if not isinstance(config, dict) or config:
            raise ValueError('PEG B 使用已回测的固定规则，不接受自定义参数')
        return {}

    def prepare(self, records, config, as_of, now):
        self.validate_config(config)
        # Reuse the existing strict publication/calendar/300-row gate; no formula change.
        bound = PegStrategy().prepare(records, {'top_n':1,'group':'all'}, as_of, now)
        return BufferedPegStrategy(bound.snapshot)

    def candidates(self):
        return sorted((r for r in self.snapshot['rows'] if r['status']=='合格'), key=lambda r:(r['peg'],r['code']))


def cooldown_active(sold_day, today):
    """A sale day is day zero. Reentry is allowed AFTER ten complete sessions."""
    cursor = date.fromisoformat(sold_day) + timedelta(days=1)
    end = date.fromisoformat(today)
    count = 0
    while cursor < end and count < 10:
        trading = is_trade_day(cursor)
        if trading is None:raise ValueError('冷却期交易日历待更新')
        count += bool(trading)
        cursor += timedelta(days=1)
    return count < 10


def seed_state(trades, account):
    quantities = {};entries = {}
    for t in trades:
        code = t['code'];prior = quantities.get(code,0)
        if t['side']=='buy':
            if not prior:entries[code]={'date':t['executed_at'][:10], 'price_fen':t['price_fen']}
            quantities[code]=prior+t['quantity']
        else:
            quantities[code]=prior-t['quantity']
            if quantities[code]<0:raise ValueError('成交记录出现负持股，不能初始化PEG B')
            if not quantities[code]:entries.pop(code,None)
    if {c:q for c,q in quantities.items() if q} != {c:p['quantity'] for c,p in account['positions'].items()}:
        raise ValueError('成交记录与持仓不一致，不能初始化原始建仓锚点')
    return {'entries':entries,'pending':{},'cooldowns':{},'sequence':account['sequence']}


def update_fill_state(state, before, after, trade):
    """Only called inside the same transaction as the committed fill, including manual fills."""
    code=trade['code']
    if trade['side']=='buy' and code not in before['positions']:
        state['entries'][code]={'date':trade['executed_at'][:10], 'price_fen':trade['price_fen']}
    if code not in after['positions']:
        if code in state['pending']:state['cooldowns'][code]=trade['executed_at'][:10]
        state['entries'].pop(code,None);state['pending'].pop(code,None)
    state['sequence']=after['sequence']


def adjusted_entry(entry, dataset):
    """Re-map the original fill on EVERY evaluation; qfq levels can rebase later.

    Tencent qfq is affine, not necessarily a multiplicative ratio. Fit the entry
    day's OHLC; a flat entry bar uses neighbouring bars only if the same affine
    transform fits. Missing/ambiguous references stop execution, never reset cost.
    """
    raw,adj=dataset['raw'],dataset['adjusted']
    if len(raw)!=len(adj) or any(a['date']!=b['date'] for a,b in zip(raw,adj)):
        raise ValueError('建仓复权与原始行情不对齐')
    index=next((i for i,b in enumerate(raw) if b['date']==entry['date']),None)
    if index is None:raise ValueError('缺少原始建仓日复权参照，保留持仓并等待行情补齐')
    for indices in [[index], list(range(max(0,index-2),min(len(raw),index+3)))]:
        pairs=[(float(raw[i][k]),float(adj[i][k])) for i in indices for k in ('open','close','high','low')]
        if not all(math.isfinite(x) and math.isfinite(y) and x>0 and y>0 for x,y in pairs):break
        mx=sum(x for x,y in pairs)/len(pairs);my=sum(y for x,y in pairs)/len(pairs)
        variance=sum((x-mx)**2 for x,y in pairs)
        if variance<1e-12:
            # Identity references are unambiguous even for a flat-price session.
            if all(abs(x-y)<1e-10 for x,y in pairs):return entry['price_fen']/100
            continue
        slope=sum((x-mx)*(y-my) for x,y in pairs)/variance
        intercept=my-slope*mx
        anchor=slope*entry['price_fen']/100+intercept
        if slope>0 and anchor>0 and max(abs(y-(slope*x+intercept)) for x,y in pairs)<=.025:
            return anchor
    raise ValueError('建仓日复权参照不明确，暂停自动交易等待核验')


def plan_selection(pool, held, state, sectors, day):
    """Same constrained ranking for entry and retention. Actual fills decide vacancies later."""
    needed={r['code'] for r in pool}|set(held)
    missing=sorted(c for c in needed if not sectors.get(c))
    if missing:raise ValueError('缺少申万一级行业分类：'+', '.join(missing))
    excluded=set(state.get('pending',{}))|{c for c,d in state.get('cooldowns',{}).items() if cooldown_active(d,day)}
    counts=Counter();active=[]
    for row in pool:
        code=row['code'];sector=sectors[code]
        if code in excluded or counts[sector]>=5:continue
        active.append({**row,'industry_l1':sector});counts[sector]+=1
    ranks={r['code']:i+1 for i,r in enumerate(active)}
    exits={c:'exit_rank' for c in held if ranks.get(c,math.inf)>9}
    exits.update({c:reason for c,reason in state.get('pending',{}).items() if c in held})
    kept=[];counts=Counter()
    for code in sorted((c for c in held if c not in exits),key=lambda c:(ranks[c],c)):
        if counts[sectors[code]] or len(kept)>=3:exits[code]='sector_quota' if counts[sectors[code]] else 'holding_quota'
        else:kept.append(code);counts[sectors[code]]+=1
    selected=list(kept)
    for row in active[:9]:
        code=row['code'];sector=sectors[code]
        if len(selected)>=3:break
        if code in held or counts[sector]:continue
        selected.append(code);counts[sector]+=1
    return {'active_candidates':active[:9], 'exit_reasons':exits, 'selected':selected}
