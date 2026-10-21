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

    def state(self,owner):
        config=self.store.config(owner)
        runtime=self.store.records.get('quant_runtime','state',owner,default={})
        latest=self.store.decisions(owner,limit=1)['items']
        return {**config,'scheduler_enabled':self.enabled,'runtime':runtime,'last_decision':latest[0] if latest else None,
                'next_check':'交易日北京时间10:00后，每分钟检查；每日一次决策'}

    def strategy(self,owner,strategy_id):
        if isinstance(strategy_id,str) and strategy_id.startswith('user-'):
            return self.users.adapter(owner,strategy_id)
        return self.registry.get(strategy_id)

    def strategies(self,owner):
        return [{**item,'origin':'official'} for item in self.registry.list()]+self.users.metadata(owner)

    def configure(self,owner,mode,strategy_id,expected_revision,strategy_revision=None):
        validator=None
        if mode=='algorithm':
            try: self.strategy(owner,strategy_id).validate_config({})
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
        return self.store.configure(owner,mode,strategy_id,expected_revision,validator=validator)

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

    async def tick(self):
        now=self.clock()
        if not session_state(now)['can_trade'] or now.hour<10: return
        configs=await asyncio.to_thread(self.store.active)
        for owner,cfg in configs:
            runtime=await asyncio.to_thread(self.store.records.get,'quant_runtime','state',owner,default={})
            if runtime.get('retry_at',0)>time.time(): continue
            key=now.date().isoformat()+':'+str(cfg['revision']).zfill(10)
            previous=await asyncio.to_thread(self.store.records.get,'quant_decision',key,owner,default=None)
            if previous and previous['status']!='running': continue
            lease=await asyncio.to_thread(self.store.records.claim,'quant:'+owner,300)
            if not lease: continue
            try:
                await self.run_owner(owner,cfg,key,lease)
                await asyncio.to_thread(self.store.records.put,'quant_runtime','state',{'status':'ok','checked_at':self.clock().isoformat()},owner)
            except (AuthError,PaperError,ValueError,KeyError,providers.ProviderError) as exc:
                await asyncio.to_thread(self.store.records.put,'quant_runtime','state',
                    {'status':'waiting','reason':str(exc),'checked_at':self.clock().isoformat(),'retry_at':time.time()+300},owner)
            except asyncio.CancelledError: raise
            except Exception:
                log.exception('Quant execution failed owner=%s',owner)
                await asyncio.to_thread(self.store.records.put,'quant_runtime','state',
                    {'status':'error','reason':'执行暂时失败，5分钟后检查原决策恢复','checked_at':self.clock().isoformat(),'retry_at':time.time()+300},owner)
            finally: await asyncio.to_thread(self.store.records.release,'quant:'+owner,lease)

    async def run_owner(self,owner,cfg,key,token):
        strategy=self.strategy(owner,cfg['strategy_id'])
        metadata=strategy.metadata()
        universe=metadata['universe']
        if any(self.paper.catalog.get(code) is None for code in universe):
            await self.paper.catalog.ensure()
        if any(self.paper.catalog.get(code) is None for code in universe):
            raise ValueError('策略股票池元数据尚未核验，等待交易所列表更新')
        signal=previous_trade_day(self.clock().date())
        if signal is None: raise ValueError('交易日历待更新')
        signal=signal.isoformat()
        decision=await asyncio.to_thread(self.store.records.get,'quant_decision',key,owner,default=None)
        if decision and decision['model_version']!=metadata['version']:
            raise ValueError('本轮模型版本已改变，请检查后切回手动再启用')
        if not decision:
            account=await asyncio.to_thread(self.paper.account,owner)
            codes=list(dict.fromkeys([*universe,*(p['code'] for p in account['positions'])]))
            dataset=await self.history_loader(codes,signal)
            proposed=strategy.decide({c:d['raw'] for c,d in dataset.items()},signal)
            weights=proposed['target_weights']
            if (len(weights)>5 or any(c not in universe or not math.isfinite(w) or not 0<=w<=.2 for c,w in weights.items())
                    or sum(weights.values())>.80000001): raise ValueError('策略输出超过风控上限')
            eligible=[c for c,d in dataset.items() if not corporate_action(d['raw'][-65:],d['adjusted'][-65:])]
            managed=set(universe)
            needed=list(dict.fromkeys([*(p['code'] for p in account['positions']),*weights]))
            for code in needed:
                await self.fresh(code)
                if not await asyncio.to_thread(self.store.records.renew,'quant:'+owner,token,300): raise ValueError('执行租约已失效')
            account=await asyncio.to_thread(self.paper.account,owner)
            if any(p['valuation_status']!='fresh' for p in account['positions']): raise ValueError('持仓报价不完整，暂停新增风险')
            current=await asyncio.to_thread(self.store.mark_peak,owner,cfg['revision'],account['equity_fen'])
            if current['mode']!='algorithm': return
            decision={**proposed,'key':key,'revision':cfg['revision'],'strategy_id':cfg['strategy_id'],
                      'date':self.clock().date().isoformat(),'status':'running','budget_fen':int(account['equity_fen']*.3),
                      'spent_fen':0,'orders':[],'results':[],'eligible':eligible,
                      'created_at':self.clock().isoformat(),'previous_close':{c:d['raw'][-1]['close'] for c,d in dataset.items()}}
            day=await asyncio.to_thread(self.store.records.get,'quant_day',decision['date'],owner,default={'spent_fen':0,'budget_fen':decision['budget_fen']})
            budget=max(0,min(day['budget_fen']-day['spent_fen'],decision['budget_fen']))
            positions={p['code']:p for p in account['positions']};cash=account['cash_fen'];total=account['market_value_fen']
            for side in ('sell','buy'):
                for code in needed:
                    if code not in managed or code not in eligible: continue
                    stock=self.paper._stock(code)
                    if 'ST' in stock['name'].upper() or '退' in stock['name']: continue
                    cached=await asyncio.to_thread(self.store.records.get,'minute_market',code+':1',default={})
                    price=valid_quote(cached,self.clock())['price_fen']
                    if abs(price/100/decision['previous_close'][code]-1)>=.045: continue
                    position=positions.get(code,{'quantity':0,'sellable':0})
                    rules=board_rules(code);step=rules['quantity_step']
                    target=int(account['equity_fen']*weights.get(code,0)/price)//step*step
                    difference=target-position['quantity']
                    quantity=(max(0,difference) if side=='buy' else min(max(0,-difference),position['sellable']))//step*step
                    quantity=min(quantity,budget//price//step*step,rules['max_quantity'])
                    if side=='buy':
                        quantity=min(quantity,max(0,cash-1000)//price//step*step,
                                     max(0,int(account['equity_fen']*.8)-total)//price//step*step)
                        if code not in positions and len(positions)>=5: continue
                    if quantity<rules['min_quantity']: continue
                    request_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f'finpulse:quant:{owner}:{key}:{code}:{side}'))
                    decision['orders'].append({'request_id':request_id,'code':code,'side':side,'quantity':quantity})
                    gross=price*quantity;budget-=gross
                    if side=='buy':
                        cash-=gross+fees(gross,side)['fees_fen'];total+=gross;positions[code]={**position,'quantity':position['quantity']+quantity}
                    else:
                        cash+=gross-fees(gross,side)['fees_fen'];total-=gross
                        if quantity==position['quantity']: positions.pop(code,None)
                        else: positions[code]={**position,'quantity':position['quantity']-quantity}
            decision=await asyncio.to_thread(self.store.save_decision,owner,decision)
        if decision['status']!='running': return
        execution={'revision':cfg['revision'],'decision_key':key,'lease_token':token,
                   'strategy_id':cfg['strategy_id'],'model_version':decision['model_version']}
        results=[]
        for order in decision['orders']:
            if not await asyncio.to_thread(self.store.records.renew,'quant:'+owner,token,300): raise ValueError('租约失效')
            # Replay a committed order without requiring a new quote or a still-open session.
            previous=await asyncio.to_thread(self.store.records.get,'paper_trade',order['request_id'],owner,default=None)
            if not previous: await self.fresh(order['code'])
            try:
                fill=await self.paper.submit(owner,**order,execution=execution)
                results.append({'request_id':order['request_id'],'code':order['code'],'status':'filled','sequence':fill['sequence']})
            except PaperError as exc:
                if exc.code in ('AUTOMATION_CHANGED','QUANT_LEASE_LOST','IDEMPOTENCY_CONFLICT'): raise
                if exc.code=='QUANT_DRAWDOWN':
                    await asyncio.to_thread(self.store.configure,owner,'manual',None,cfg['revision'],'回撤达到15%，自动暂停。')
                results.append({'request_id':order['request_id'],'code':order['code'],'status':'skipped','reason':exc.message,'code_reason':exc.code})
        await asyncio.to_thread(self.store.finish,owner,key,cfg['revision'],'completed',results,
                                None if decision['orders'] else '未产生可执行订单：阈值、公司行为、费用或风险限制。')
