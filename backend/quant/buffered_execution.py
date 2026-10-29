"""PEG B daily plan and two persisted execution phases on the existing paper ledger."""
import asyncio
import copy
import math
import uuid
from datetime import datetime, timedelta

from .. import providers
from ..news_cleaning import SHANGHAI
from ..paper_rules import PaperError, board_rules, cached_quote, fees, valid_quote
from ..trading_calendar import previous_trade_day
from .buffered_peg import (
    BUFFERED_PEG_ID, BUFFERED_VERSION, INDUSTRIES, INDUSTRY_VERSION,
    adjusted_entry, industry_at, plan_selection,
)
from .features import corporate_action

REASON='PEG B：3股，约束排名前9保留，每一级行业1股/资金60%；12%价格退出后冷却10个交易日；5个百分点免微调。'
EXECUTION_NOTE='线上使用有效分钟报价整笔模拟成交及现有费用；未复刻回测的1%分钟参与率、部分成交及20bp最低摩擦成本；公司行动待核验时暂停相关股票成交。'


def decision_mark(cached,now):
    cutoff=now.astimezone(SHANGHAI).replace(hour=10,minute=0,second=0,microsecond=0)
    bars=[]
    for bar in cached['candles']:
        stamp=datetime.fromisoformat(bar['date'])
        stamp=stamp.replace(tzinfo=SHANGHAI) if stamp.tzinfo is None else stamp.astimezone(SHANGHAI)
        if cutoff-timedelta(minutes=3)<=stamp<=cutoff:bars.append((stamp,bar))
    if not bars:raise ValueError('缺少10:00前的有效决策报价')
    bar=max(bars,key=lambda b:b[0])[1]
    return cached_quote({**cached,'candles':[bar]})['price_fen']


def order_for(owner,key,code,side,quantity):
    return {'request_id':str(uuid.uuid5(uuid.NAMESPACE_URL,f'finpulse:quant:{owner}:{key}:{code}:{side}')),
            'code':code,'side':side,'quantity':quantity}


def sell_quantity(position,quantity,code):
    rule=board_rules(code)
    quantity=min(quantity,position['sellable'],rule['max_quantity'])
    # A complete available odd remainder can be liquidated in one request.
    if quantity==position['sellable'] and quantity<rule['min_quantity']:return quantity
    quantity=quantity//rule['quantity_step']*rule['quantity_step']
    return quantity if quantity>=rule['min_quantity'] else 0


async def histories(service,codes,signal,entries):
    result=await service.history_loader(codes,signal) if codes else {}
    for code in codes:
        entry=entries.get(code)
        if not entry or result[code]['raw'][0]['date']<=entry['date']:continue
        # The anchor is never reset when it ages out of the ordinary 230-day cache.
        key=code+':'+signal+':entry:'+entry['date']
        item=await asyncio.to_thread(service.store.records.get,'quant_history',key,default=None)
        if not item:
            start=(datetime.fromisoformat(entry['date'])-timedelta(days=7)).date().isoformat()
            item=await providers.akshare_worker('quant_daily',code,start,signal,retry=False)
            await asyncio.to_thread(service.store.records.put,'quant_history',key,item)
        if not item['raw'] or item['raw'][-1]['date']!=signal or item['adjusted'][-1]['date']!=signal:
            raise ValueError('长期持仓复权行情未覆盖上一交易日')
        result[code]=item
    return result


async def create_plan(service,owner,cfg,key,token,signal):
    strategy=await asyncio.to_thread(service.strategy(owner,BUFFERED_PEG_ID).prepare,service.store.records,{},signal,service.clock())
    account=await asyncio.to_thread(service.paper.account,owner)
    held={p['code']:p for p in account['positions']}
    state=copy.deepcopy(await asyncio.to_thread(service.store.records.get,'peg_b_state','state',owner,default=None))
    if not state:raise ValueError('PEG B建仓锚点尚未初始化')
    dataset=await histories(service,list(held),signal,state['entries'])
    price_exits={}
    for code in held:
        if code not in state['entries']:raise ValueError(code+' 缺少原始建仓锚点')
        if state['entries'][code]['date']>signal:continue
        anchor=adjusted_entry(state['entries'][code],dataset[code])
        if float(dataset[code]['adjusted'][-1]['close'])<=anchor*.88+1e-10:
            price_exits[code]='price_hard'
    if price_exits:
        state=await asyncio.to_thread(service.store.latch_buffered_exits,owner,cfg['revision'],price_exits,state['entries'])
    pool=strategy.candidates()
    sectors={c:industry_at(c,signal) for c in {r['code'] for r in pool}|set(held)}
    selection=plan_selection(pool,held,state,sectors,service.clock().date().isoformat())
    needed=list(dict.fromkeys([*held,*(r['code'] for r in selection['active_candidates'])]))
    if any(service.paper.catalog.get(c) is None for c in needed):await service.paper.catalog.ensure()
    if any(service.paper.catalog.get(c) is None for c in needed):raise ValueError('PEG B候选元数据尚未核验')
    dataset.update(await histories(service,[c for c in needed if c not in dataset],signal,{}))
    marks={};eligible=[];skipped={}
    for code in needed:
        board_rules(code)
        raw=await service.fresh(code)
        # Freeze the mark available at 10:00, never use a later candle for selection/sizing.
        marks[code]=decision_mark(raw,service.clock())
        data=dataset[code]
        stock=service.paper._stock(code)
        if corporate_action(data['raw'][-2:],data['adjusted'][-2:]):
            skipped[code]='最近交易日公司行动或复权变化待核验，暂停自动成交'
        elif 'ST' in stock['name'].upper() or '退' in stock['name']:
            skipped[code]='风险警示或退市证券，暂停自动成交'
        else:eligible.append(code)
        if not await asyncio.to_thread(service.store.records.renew,'quant:'+owner,token,300):raise ValueError('PEG B执行租约失效')
    equity=account['cash_fen']+sum(p['quantity']*marks[c] for c,p in held.items())
    per_slot=max(0,math.floor((equity-math.ceil(equity*.004)-3000)/3))
    exits=selection['exit_reasons'];band_keep=[];orders=[]
    for code,p in sorted(held.items()):
        current=p['quantity']*marks[code]
        sector_value=sum(q['quantity']*marks[c] for c,q in held.items() if sectors[c]==sectors[code])
        if code not in exits and abs(current-per_slot)<=.05*equity and sector_value<=.6*equity:
            band_keep.append(code);continue
        quantity=p['quantity'] if code in exits else max(0,(current-per_slot)//marks[code])
        quantity=sell_quantity(p,quantity,code)
        if quantity:orders.append(order_for(owner,key,code,'sell',quantity))
        elif code in exits:skipped[code]='应退出，当前受T+1或最小申报数量限制；持仓继续占用名额'
    selected=selection['selected']
    snap={k:strategy.snapshot[k] for k in ('snapshot_id','method_version','published_at','market_date')}
    return {'key':key,'revision':cfg['revision'],'strategy_id':BUFFERED_PEG_ID,'strategy_config':{},
            'model_version':BUFFERED_VERSION,'as_of':signal,'date':service.clock().date().isoformat(),
            'status':'running','created_at':service.clock().isoformat(),'reason':REASON,
            'peg_snapshot':snap,'peg_selection':[r for r in selection['active_candidates'] if r['code'] in selected],
            'industry_source':{'observed_at':INDUSTRIES['observed_at'],'sha256_prefix':INDUSTRY_VERSION},
            'target_weights':{c:per_slot/equity for c in selected},'scores':{r['code']:r['peg'] for r in pool},
            'eligible':eligible,'previous_close':{c:dataset[c]['raw'][-1]['close'] for c in needed},
            'sectors':{c:sectors[c] for c in needed},'active_candidates':selection['active_candidates'],
            'exit_reasons':exits,'price_exits':price_exits,'band_keep':band_keep,'marks_fen':marks,
            'per_slot_fen':per_slot,'decision_equity_fen':equity,'orders':orders,'results':[],
            'spent_fen':0,'budget_fen':equity*2,'skipped_targets':skipped,'execution_note':EXECUTION_NOTE}


async def buy_plan(service,owner,cfg,decision):
    account=await asyncio.to_thread(service.paper.account,owner)
    held={p['code']:p for p in account['positions']};sectors=decision['sectors'];exits=decision['exit_reasons']
    counts={sectors[c] for c in held};entrants=[]
    sold={r['code'] for r in decision['results'] if r['status']=='filled' and r['side']=='sell' and r['code'] not in held}
    for row in decision['active_candidates']:
        code=row['code'];sector=sectors[code]
        if len(held)+len(entrants)>=3:break
        if code in held or code in sold or code in exits or sector in counts:continue
        entrants.append(code);counts.add(sector)
    ranks={r['code']:i for i,r in enumerate(decision['active_candidates'])}
    kept=[c for c in held if c not in exits]
    selected=kept+entrants
    target={c:decision['per_slot_fen']/decision['decision_equity_fen'] for c in selected}
    sell_codes={o['code'] for o in decision['orders'] if o['side']=='sell'}
    skipped=dict(decision['skipped_targets']);orders=list(decision['orders']);cash=account['cash_fen']
    for code in sorted(selected,key=lambda c:(c in held,ranks.get(c,999),c)):
        if code in decision['band_keep'] or code in sell_codes:continue
        rule=board_rules(code);mark=decision['marks_fen'][code]
        current=held.get(code,{}).get('quantity',0)*mark
        quantity=max(0,(decision['per_slot_fen']-current)//mark)//rule['quantity_step']*rule['quantity_step']
        quantity=min(quantity,rule['max_quantity'])
        try:
            quote=valid_quote(await service.fresh(code),service.clock());price=quote['price_fen']
        except PaperError as exc:
            skipped[code]=exc.message;continue
        quantity=min(quantity,max(0,cash-500)//price//rule['quantity_step']*rule['quantity_step'])
        while quantity>=rule['min_quantity'] and quantity*price+fees(quantity*price,'buy')['fees_fen']>cash:
            quantity-=rule['quantity_step']
        if quantity<rule['min_quantity']:
            skipped[code]='可用现金或整手不足，本轮不顺延换股';continue
        orders.append(order_for(owner,decision['key'],code,'buy',quantity))
        cash-=price*quantity+fees(price*quantity,'buy')['fees_fen']
    selection=[r for r in decision['active_candidates'] if r['code'] in selected]
    return await asyncio.to_thread(service.store.buffered_progress,owner,decision['key'],cfg['revision'],
                                   orders=orders,buy_planned=True,target_weights=target,peg_selection=selection,skipped_targets=skipped)


async def execute_phase(service,owner,cfg,decision,token,side):
    results=list(decision['results']);seen={r['request_id'] for r in results}
    execution={'revision':cfg['revision'],'decision_key':decision['key'],'lease_token':token,
               'strategy_id':BUFFERED_PEG_ID,'model_version':BUFFERED_VERSION,
               'peg_snapshot_id':decision['peg_snapshot']['snapshot_id']}
    deadline=606 if side=='sell' else 611
    for order in decision['orders']:
        if order['side']!=side or order['request_id'] in seen:continue
        if not await asyncio.to_thread(service.store.records.renew,'quant:'+owner,token,300):raise ValueError('PEG B执行租约失效')
        previous=await asyncio.to_thread(service.store.records.get,'paper_trade',order['request_id'],owner,default=None)
        result={'request_id':order['request_id'],'code':order['code'],'side':side}
        try:
            if not previous:
                if service.clock().hour*60+service.clock().minute>=deadline:
                    raise PaperError('PEG_WINDOW_CLOSED','错过本轮执行窗口，未追溯成交；下一交易日重新评估')
                await service.fresh(order['code'])
            fill=await service.paper.submit(owner,**order,execution=execution)
            result.update(status='filled',sequence=fill['sequence'])
        except PaperError as exc:
            if exc.code in ('AUTOMATION_CHANGED','QUANT_LEASE_LOST','IDEMPOTENCY_CONFLICT'):raise
            result.update(status='skipped',reason=exc.message,code_reason=exc.code)
        results.append(result)
    return await asyncio.to_thread(service.store.buffered_progress,owner,decision['key'],cfg['revision'],
                                   results=results,**{side+'_done':True})


async def run_buffered(service,owner,cfg,key,token):
    now=service.clock();minute=now.hour*60+now.minute
    signal=previous_trade_day(now.date())
    if signal is None:raise ValueError('PEG B交易日历待更新')
    decision=await asyncio.to_thread(service.store.records.get,'quant_decision',key,owner,default=None)
    if not decision:
        if minute>=611:
            # Enabling or restarting after the morning window never executes a stale allocation.
            decision={'key':key,'revision':cfg['revision'],'strategy_id':BUFFERED_PEG_ID,'strategy_config':{},
                      'model_version':BUFFERED_VERSION,'as_of':signal.isoformat(),'date':now.date().isoformat(),
                      'status':'completed','target_weights':{},'orders':[],'results':[],
                      'reason':'已错过PEG B今日10:02–10:11执行窗口；保留实际持仓，下一交易日重新评估。'}
        else:decision=await create_plan(service,owner,cfg,key,token,signal.isoformat())
        decision=await asyncio.to_thread(service.store.save_decision,owner,decision)
    if decision['model_version']!=BUFFERED_VERSION:raise ValueError('PEG B计划版本已改变，请暂停后重新启用')
    if decision['status']!='running':return
    if minute<602:return
    if not decision.get('sell_done'):decision=await execute_phase(service,owner,cfg,decision,token,'sell')
    if service.clock().hour*60+service.clock().minute<607:return
    if not decision.get('buy_planned'):
        if service.clock().hour*60+service.clock().minute>=611:
            decision=await asyncio.to_thread(service.store.buffered_progress,owner,key,cfg['revision'],buy_planned=True)
        else:decision=await buy_plan(service,owner,cfg,decision)
    if not decision.get('buy_done'):decision=await execute_phase(service,owner,cfg,decision,token,'buy')
    account=await asyncio.to_thread(service.paper.account,owner)
    positions={p['code']:{'quantity':p['quantity'],'weight':p['market_value_fen']/account['equity_fen']} for p in account['positions']}
    outcome={'positions':positions,'differences':{c:{'target_weight':decision['target_weights'].get(c,0),
             'actual_weight':positions.get(c,{}).get('weight',0)} for c in dict.fromkeys([*positions,*decision['target_weights']])
             if abs(decision['target_weights'].get(c,0)-positions.get(c,{}).get('weight',0))>.001},
             'note':'5个百分点以内保留原仓位；T+1或未成交持仓继续占用名额，次日重新评估。'}
    await asyncio.to_thread(service.store.finish,owner,key,cfg['revision'],'completed',decision['results'],REASON,outcome)
