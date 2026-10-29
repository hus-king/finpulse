"""Server-only paper automation; persistent plans survive process restarts."""
import asyncio
import logging
import math
import time
import uuid
from datetime import datetime, timedelta

from .. import providers
from ..auth import AuthError
from ..news_cleaning import SHANGHAI
from ..paper_rules import PaperError, board_rules, fees, session_state, valid_quote
from ..trading_calendar import previous_trade_day
from .features import corporate_action, features
from .store import QuantStore
from .strategy import StrategyRegistry
from .peg_strategy import PEG_STRATEGY_ID, price_change_limit
from .buffered_peg import BUFFERED_PEG_ID
from .user_store import UserStrategyStore

log=logging.getLogger(__name__)


class QuantService:
    def __init__(self,paper,enabled=False,registry=None,clock=None):
        self.paper=paper;self.store=QuantStore(paper.store);self.registry=registry or StrategyRegistry()
        self.clock=clock or (lambda:datetime.now(SHANGHAI));self.enabled=enabled;self.task=None
        self.history_loader=self.load_histories
        self.users=UserStrategyStore(self.store)

    def start(self):
        if self.enabled: self.task=asyncio.create_task(self.loop())

    async def close(self):
        if self.task:
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)

    async def loop(self):
        while True:
            try: await self.tick()
            except asyncio.CancelledError: raise
            except Exception: log.exception('Quant scheduler tick failed')
            await asyncio.sleep(60)

    def opening_pending(self,owner,config,now):
        if config.get('mode')!='algorithm' or config.get('strategy_id')!=PEG_STRATEGY_ID:return False
        marker=self.store.records.get('quant_opening','once',owner,default={})
        return bool(marker.get('reset_id') and marker.get('strategy_id')==PEG_STRATEGY_ID and
                    type(marker.get('config_revision')) is int and marker['config_revision']==config['revision'] and
                    marker.get('trade_date')==now.date().isoformat())

    def state(self,owner):
        config=self.store.config(owner)
        runtime=self.store.records.get('quant_runtime','state',owner,default={})
        latest=self.store.decisions(owner,limit=1)['items']
        opening=self.opening_pending(owner,config,self.clock())
        if latest and latest[0].get('revision')==config['revision'] and latest[0].get('date')==self.clock().date().isoformat() and latest[0]['status']!='running':opening=False
        return {**config,'scheduler_enabled':self.enabled,'runtime':runtime,'last_decision':latest[0] if latest else None,
                'next_check':('交易日10:00决策；10:02–10:06卖出、10:07–10:11买入；错过窗口次日重新评估' if config.get('strategy_id')==BUFFERED_PEG_ID else
                    '重置后首次PEG建仓：今日09:30后每分钟检查，有效报价就绪后执行；之后恢复10:00' if opening else '交易日北京时间10:00后，每分钟检查；每日一次决策')}

    def strategy(self,owner,strategy_id):
        if isinstance(strategy_id,str) and strategy_id.startswith('user-'):
            return self.users.adapter(owner,strategy_id)
        return self.registry.get(strategy_id)

    def strategies(self,owner):
        return [{**item,'origin':'official'} for item in self.registry.list()]+self.users.metadata(owner)

    def configure(self,owner,mode,strategy_id,expected_revision,strategy_revision=None,strategy_config=None):
        validator=None
        normalized={}
        if mode=='algorithm':
            try: normalized=self.strategy(owner,strategy_id).validate_config(strategy_config or {})
            except ValueError as exc: raise PaperError('STRATEGY_UNAVAILABLE',str(exc),422) from None
            if strategy_id.startswith('user-'):
                if type(strategy_revision) is not int or strategy_revision<1:
                    raise PaperError('STRATEGY_REVISION_REQUIRED','启用我的策略需要指定当前策略版本。',422)
                def validator(conn):
                    record=self.paper.store._get(conn,'user_strategy',strategy_id,owner,None)
                    if not record:raise PaperError('STRATEGY_NOT_FOUND','未找到你的策略。',404)
                    if record['revision']!=strategy_revision:
                        raise PaperError('STRATEGY_REVISION_CONFLICT','策略已更新，请刷新后重新选择。')
                    self.users.from_record(record)
            elif strategy_id==BUFFERED_PEG_ID:
                validator=lambda conn:self.store.initialize_buffered(conn,owner)
        return self.store.configure(owner,mode,strategy_id,expected_revision,validator=validator,strategy_config=normalized)

    async def load_histories(self,codes,signal):
        result={}
        for code in codes:
            key=code+':'+signal
            item=await asyncio.to_thread(self.store.records.get,'quant_history',key,default=None)
            if not item:
                start=(datetime.fromisoformat(signal)-timedelta(days=230)).date().isoformat()
                # Disposable child: cancellation/timeout cannot leave unbounded network threads.
                item=await providers.akshare_worker('quant_daily',code,start,signal,retry=False)
                await asyncio.to_thread(self.store.records.put,'quant_history',key,item)
            raw,adj=item['raw'],item['adjusted']
            if not raw or raw[-1]['date']!=signal or adj[-1]['date']!=signal:
                raise ValueError(code+' 日线尚未覆盖上一交易日')
            features(raw);corporate_action(raw,adj)
            result[code]=item
        return result

    async def fresh(self,code):
        quote=await self.paper.quote(code,False)
        task=self.paper.minute_market.tasks.get(code+':1')
        if task: await asyncio.shield(task)
        raw=await asyncio.to_thread(self.store.records.get,'minute_market',code+':1',default={})
        valid_quote(raw,self.clock())
        return raw

    def save_runtime(self,owner,revision,payload):
        try:return self.store.save_runtime(owner,revision,payload)
        except AuthError:return False

    async def tick(self):
        now=self.clock()
        if not session_state(now)['can_trade']: return
        configs=await asyncio.to_thread(self.store.active)
        for owner,cfg in configs:
            opening=self.opening_pending(owner,cfg,now)
            if now.hour<10 and not opening:continue
            runtime=await asyncio.to_thread(self.store.records.get,'quant_runtime','state',owner,default={})
            if runtime.get('retry_at',0)>time.time(): continue
            key=now.date().isoformat()+':'+str(cfg['revision']).zfill(10)
            previous=await asyncio.to_thread(self.store.records.get,'quant_decision',key,owner,default=None)
            if not previous and cfg['strategy_id'] in (PEG_STRATEGY_ID,BUFFERED_PEG_ID):
                await asyncio.to_thread(self.store.expire_peg_plans,owner,now.date().isoformat())
            if previous and previous['status']!='running': continue
            lease=await asyncio.to_thread(self.store.records.claim,'quant:'+owner,300)
            if not lease: continue
            try:
                await self.run_owner(owner,cfg,key,lease)
                await asyncio.to_thread(self.save_runtime,owner,cfg['revision'],{'status':'ok','checked_at':self.clock().isoformat()})
            except (AuthError,PaperError,ValueError,KeyError,providers.ProviderError) as exc:
                delay=60 if opening and isinstance(exc,PaperError) and exc.code in ('QUOTE_STALE','QUOTE_UNAVAILABLE') else 300
                await asyncio.to_thread(self.save_runtime,owner,cfg['revision'],
                    {'status':'waiting','reason':str(exc),'checked_at':self.clock().isoformat(),'retry_at':time.time()+delay})
            except asyncio.CancelledError: raise
            except Exception:
                log.exception('Quant execution failed owner=%s',owner)
                await asyncio.to_thread(self.save_runtime,owner,cfg['revision'],
                    {'status':'error','reason':'执行暂时失败，5分钟后检查原决策恢复','checked_at':self.clock().isoformat(),'retry_at':time.time()+300})
            finally: await asyncio.to_thread(self.store.records.release,'quant:'+owner,lease)

    async def run_owner(self,owner,cfg,key,token):
        if cfg['strategy_id']==BUFFERED_PEG_ID:
            from .buffered_execution import run_buffered
            return await run_buffered(self,owner,cfg,key,token)
        strategy=self.strategy(owner,cfg['strategy_id'])
        is_peg=cfg['strategy_id']==PEG_STRATEGY_ID
        signal=previous_trade_day(self.clock().date())
        if signal is None: raise ValueError('交易日历待更新')
        signal=signal.isoformat()
        decision=await asyncio.to_thread(self.store.records.get,'quant_decision',key,owner,default=None)
        if decision and decision['model_version']!=strategy.metadata()['version']:
            raise ValueError('本轮模型版本已改变，请检查后切回手动再启用')
        if not decision:
            if is_peg:strategy=await asyncio.to_thread(strategy.prepare,self.store.records,cfg.get('strategy_config',{}),signal,self.clock())
            universe=strategy.metadata()['universe']
            account=await asyncio.to_thread(self.paper.account,owner)
            codes=list(dict.fromkeys([*universe,*(p['code'] for p in account['positions'])]))
            if any(self.paper.catalog.get(code) is None for code in codes):await self.paper.catalog.ensure()
            if any(self.paper.catalog.get(code) is None for code in codes):raise ValueError('策略股票池元数据尚未核验，等待交易所列表更新')
            dataset=await self.history_loader(codes,signal)
            proposed=strategy.decide({c:d['raw'] for c,d in dataset.items()},signal)
            weights=proposed['target_weights']
            if (len(weights)>5 or any(c not in universe or not math.isfinite(w) or not 0<=w<=(1 if is_peg else .2) for c,w in weights.items())
                    or sum(weights.values())>(1.00000001 if is_peg else .80000001)): raise ValueError('策略输出超过风控上限')
            # PEG uses the latest ranking, not 65-day price features. A distant
            # ex-date must not exclude a new position for three months.
            window=2 if is_peg else 65
            eligible=[c for c,d in dataset.items() if not corporate_action(d['raw'][-window:],d['adjusted'][-window:])]
            managed=set(codes if is_peg else universe)
            needed=list(dict.fromkeys([*(p['code'] for p in account['positions']),*weights]))
            for code in needed:
                await self.fresh(code)
                if not await asyncio.to_thread(self.store.records.renew,'quant:'+owner,token,300): raise ValueError('执行租约已失效')
            account=await asyncio.to_thread(self.paper.account,owner)
            if any(p['valuation_status']!='fresh' for p in account['positions']): raise ValueError('持仓报价不完整，暂停新增风险')
            current=await asyncio.to_thread(self.store.mark_peak,owner,cfg['revision'],account['equity_fen'])
            if current['mode']!='algorithm': return
            prices={}
            if is_peg:
                for code in needed:
                    raw=await asyncio.to_thread(self.store.records.get,'minute_market',code+':1',default={})
                    prices[code]=valid_quote(raw,self.clock())['price_fen']
                # Upper bound for selling the current book and buying a new one:
                # actual fees are lower when existing positions can be retained.
                reserve=sum(fees(p['market_value_fen'],'sell')['fees_fen'] for p in account['positions'])+math.ceil(account['equity_fen']*.00031)+len(weights)*500
                targets=strategy.allocate(prices,account['equity_fen'],reserve)
                weights={code:prices[code]*quantity/account['equity_fen'] for code,quantity in targets.items()}
                proposed.update(target_weights=weights,target_quantities=targets,skipped_targets={})
            decision={**proposed,'key':key,'revision':cfg['revision'],'strategy_id':cfg['strategy_id'],
                      'date':self.clock().date().isoformat(),'status':'running','budget_fen':int(account['equity_fen']*.3),
                      'spent_fen':0,'orders':[],'results':[],'eligible':eligible,
                      'created_at':self.clock().isoformat(),'previous_close':{c:d['raw'][-1]['close'] for c,d in dataset.items()}}
            budget=0
            if not is_peg:
                day=await asyncio.to_thread(self.store.records.get,'quant_day',decision['date'],owner,default={'spent_fen':0,'budget_fen':decision['budget_fen']})
                budget=max(0,min(day['budget_fen']-day['spent_fen'],decision['budget_fen']))
            positions={p['code']:p for p in account['positions']};cash=account['cash_fen'];total=account['market_value_fen']
            for side in ('sell','buy'):
                # Establish missing names before topping up existing positions.
                for code in (sorted(needed,key=lambda c:c in positions) if is_peg and side=='buy' else needed):
                    if code not in managed:continue
                    if code not in eligible:
                        if is_peg and code in weights:decision['skipped_targets'][code]='最近交易日有除权或复权参照变化，暂不新增风险'
                        continue
                    stock=self.paper._stock(code)
                    if 'ST' in stock['name'].upper() or '退' in stock['name']:
                        if is_peg and code in weights:decision['skipped_targets'][code]='风险警示或退市股票暂停自动买入'
                        continue
                    cached=await asyncio.to_thread(self.store.records.get,'minute_market',code+':1',default={})
                    price=valid_quote(cached,self.clock())['price_fen']
                    if abs(price/100/decision['previous_close'][code]-1)>=(price_change_limit(code) if is_peg else .045):
                        if is_peg and code in weights:decision['skipped_targets'][code]='报价接近该板块涨跌停边界，暂停自动成交'
                        continue
                    position=positions.get(code,{'quantity':0,'sellable':0})
                    rules=board_rules(code);step=rules['quantity_step']
                    target=decision['target_quantities'].get(code,0) if is_peg else int(account['equity_fen']*weights.get(code,0)/price)//step*step
                    difference=target-position['quantity']
                    quantity=(max(0,difference) if side=='buy' else min(max(0,-difference),position['sellable']))//step*step
                    if is_peg and quantity>rules['max_quantity']:decision['skipped_targets'][code]='达到单笔申报股数上限，剩余目标留待下次评估'
                    quantity=min(quantity,rules['max_quantity'])
                    if not is_peg:quantity=min(quantity,budget//price//step*step)
                    if side=='buy':
                        cash_reserve=fees(price*quantity,'buy')['fees_fen'] if is_peg else 1000
                        quantity=min(quantity,max(0,cash-cash_reserve)//price//step*step)
                        if not is_peg:quantity=min(quantity,max(0,int(account['equity_fen']*.8)-total)//price//step*step)
                        if code not in positions and len(positions)>=5:
                            if is_peg:decision['skipped_targets'][code]='已有5只持仓，等待旧股票满足T+1后卖出'
                            continue
                    liquidating_remainder=is_peg and side=='sell' and 0<quantity==position['sellable']<rules['min_quantity']
                    if quantity<rules['min_quantity'] and not liquidating_remainder:
                        if is_peg and side=='buy' and difference>0:decision['skipped_targets'][code]='整手或可用现金限制，未达到最小买入数量'
                        continue
                    request_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f'finpulse:quant:{owner}:{key}:{code}:{side}'))
                    decision['orders'].append({'request_id':request_id,'code':code,'side':side,'quantity':quantity})
                    gross=price*quantity
                    if not is_peg:budget-=gross
                    if side=='buy':
                        cash-=gross+fees(gross,side)['fees_fen'];total+=gross;positions[code]={**position,'quantity':position['quantity']+quantity}
                    else:
                        cash+=gross-fees(gross,side)['fees_fen'];total-=gross
                        if quantity==position['quantity']: positions.pop(code,None)
                        else: positions[code]={**position,'quantity':position['quantity']-quantity}
            if is_peg:
                decision['budget_fen']=sum(prices[o['code']]*o['quantity'] for o in decision['orders'])
            decision=await asyncio.to_thread(self.store.save_decision,owner,decision)
        if decision['status']!='running': return
        execution={'revision':cfg['revision'],'decision_key':key,'lease_token':token,
                   'strategy_id':cfg['strategy_id'],'model_version':decision['model_version']}
        if is_peg:execution['peg_snapshot_id']=decision['peg_snapshot']['snapshot_id']
        results=[];paused=False
        for order in decision['orders']:
            if not await asyncio.to_thread(self.store.records.renew,'quant:'+owner,token,300): raise ValueError('租约失效')
            # Replay a committed order without requiring a new quote or a still-open session.
            previous=await asyncio.to_thread(self.store.records.get,'paper_trade',order['request_id'],owner,default=None)
            if not previous: await self.fresh(order['code'])
            try:
                fill=await self.paper.submit(owner,**order,execution=execution)
                results.append({'request_id':order['request_id'],'code':order['code'],'status':'filled','sequence':fill['sequence']})
            except PaperError as exc:
                if exc.code=='AUTOMATION_CHANGED':
                    await asyncio.to_thread(self.store.finish,owner,key,cfg['revision'],'paused',results,exc.message)
                    raise
                if exc.code in ('QUANT_LEASE_LOST','IDEMPOTENCY_CONFLICT'):raise
                if exc.code=='QUANT_DRAWDOWN':
                    await asyncio.to_thread(self.store.configure,owner,'manual',None,cfg['revision'],'回撤达到15%，自动暂停。')
                    paused=True
                results.append({'request_id':order['request_id'],'code':order['code'],'status':'skipped','reason':exc.message,'code_reason':exc.code})
                if paused:break
        outcome=None
        if is_peg:
            account=await asyncio.to_thread(self.paper.account,owner)
            positions={p['code']:{'quantity':p['quantity'],'weight':p['market_value_fen']/account['equity_fen']} for p in account['positions']}
            outcome={'positions':positions,'differences':{code:{'target_weight':decision['target_weights'].get(code,0),
                     'actual_weight':positions.get(code,{}).get('weight',0)} for code in dict.fromkeys([*decision['target_weights'],*positions])
                     if abs(decision['target_weights'].get(code,0)-positions.get(code,{}).get('weight',0))>.001},
                     'note':'目标与实际可能因T+1、整手、行情或费用限制而不同；未完成部分下个交易日重新评估。'}
        reason='回撤达到15%，本轮计划已暂停。' if paused else ((decision['reason'] if is_peg else None) if decision['orders'] else '未产生可执行订单：阈值、公司行为、费用或风险限制。')
        await asyncio.to_thread(self.store.finish,owner,key,cfg['revision'],'paused' if paused else 'completed',results,reason,outcome)
