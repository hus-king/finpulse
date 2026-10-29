"""Automation state serialized by the same user row as paper money."""
import json
import math
import time
from contextlib import contextmanager

from ..auth import AuthError
from ..paper_rules import PaperError, new_account, valid_quote, value_account
from .peg_strategy import PEG_STRATEGY_ID, price_change_limit
from .buffered_peg import BUFFERED_PEG_ID, seed_state


def default_config():
    return {'mode':'manual','strategy_id':None,'strategy_config':{},'revision':0,'peak_equity_fen':20_000_000,'reason':None}


class QuantStore:
    def __init__(self, paper_store):
        self.paper=paper_store;self.records=paper_store.records;self.db=paper_store.db

    @contextmanager
    def locked(self,owner):
        with self.db.transaction() as conn:
            suffix=' FOR UPDATE' if self.db.dialect=='mysql' else ''
            user=conn.execute('SELECT id,is_active FROM users WHERE id=?'+suffix,(owner,)).fetchone()
            if not user: raise AuthError(401,'请先登录。','AUTH_REQUIRED')
            if not user['is_active']: raise AuthError(403,'这个账号已停用。','ACCOUNT_DISABLED')
            yield conn

    def config(self,owner):
        return self.records.get('quant_config','config',owner,default=None) or default_config()

    def initialize_buffered(self,conn,owner):
        account=self.paper._get(conn,'paper_account','account',owner,new_account())
        state=self.paper._get(conn,'peg_b_state','state',owner,None)
        if state is None:
            rows=conn.execute("SELECT payload FROM research_records WHERE namespace='paper_trade' AND owner=?",(owner,)).fetchall()
            trades=sorted((json.loads(r['payload']) for r in rows),key=lambda r:r['sequence'])
            try:state=seed_state(trades,account)
            except ValueError as exc:raise PaperError('PEG_STATE_INVALID',str(exc),422) from None
            self.paper._put(conn,'peg_b_state','state',owner,state)
        if state['sequence']!=account['sequence'] or set(state['entries'])!=set(account['positions']):
            raise PaperError('PEG_STATE_INVALID','PEG B锚点与成交账本不一致，请检查账户。')
        return state

    def latch_buffered_exits(self,owner,revision,exits,entries):
        # A known close-based stop must survive later quote/planning failures.
        with self.locked(owner) as conn:
            cfg=self.paper._get(conn,'quant_config','config',owner,default_config())
            state=self.initialize_buffered(conn,owner)
            if (cfg['mode']!='algorithm' or cfg['revision']!=revision or cfg['strategy_id']!=BUFFERED_PEG_ID or
                    any(state['entries'].get(c)!=entries[c] for c in exits)):
                raise PaperError('AUTOMATION_CHANGED','PEG B配置或建仓锚点已改变。')
            state['pending'].update(exits)
            self.paper._put(conn,'peg_b_state','state',owner,state)
        return state

    def configure(self,owner,mode,strategy_id,expected_revision,reason=None,validator=None,strategy_config=None):
        with self.locked(owner) as conn:
            old=self.paper._get(conn,'quant_config','config',owner,default_config())
            if old['revision']!=expected_revision:
                raise PaperError('AUTOMATION_CONFLICT','操作方式已变化，请刷新后再试。')
            if validator:validator(conn)
            new={**old,'mode':mode,'strategy_id':strategy_id if mode=='algorithm' else None,
                 'strategy_config':(strategy_config or {}) if mode=='algorithm' else {},
                 'revision':old['revision']+1,'reason':reason,'updated_at':int(time.time())}
            self.paper._put(conn,'quant_config','config',owner,new)
        return new

    def save_runtime(self,owner,revision,payload):
        # An obsolete worker must not impose its retry timer on a reset account.
        with self.locked(owner) as conn:
            cfg=self.paper._get(conn,'quant_config','config',owner,default_config())
            if cfg['revision']!=revision or cfg['mode']!='algorithm':return False
            self.paper._put(conn,'quant_runtime','state',owner,{**payload,'revision':revision})
        return True

    def active(self):
        with self.db.connection() as conn:
            rows=conn.execute("SELECT r.owner,r.payload FROM research_records r JOIN users u ON u.id=r.owner WHERE r.namespace='quant_config' AND r.record_key='config' AND u.is_active=1").fetchall()
        return [(r['owner'],json.loads(r['payload'])) for r in rows if json.loads(r['payload']).get('mode')=='algorithm']

    def decisions(self,owner,before=None,limit=20):
        sql="SELECT record_key,payload FROM research_records WHERE namespace='quant_decision' AND owner=?"
        params=[owner]
        if before: sql+=' AND record_key<?';params.append(before)
        sql+=' ORDER BY record_key DESC LIMIT ?';params.append(limit+1)
        with self.db.connection() as conn: rows=conn.execute(sql,params).fetchall()
        items=[json.loads(r['payload']) for r in rows[:limit]]
        return {'items':items,'next_before':rows[limit-1]['record_key'] if len(rows)>limit else None}

    def save_decision(self,owner,decision):
        with self.locked(owner) as conn:
            cfg=self.paper._get(conn,'quant_config','config',owner,default_config())
            if cfg['mode']!='algorithm' or cfg['revision']!=decision['revision']:
                raise PaperError('AUTOMATION_CHANGED','托管配置已改变，本轮决策停止。')
            existing=self.paper._get(conn,'quant_decision',decision['key'],owner,None)
            if existing: return existing
            if decision.get('strategy_id')==BUFFERED_PEG_ID:
                self.initialize_buffered(conn,owner)
            self.paper._put(conn,'quant_decision',decision['key'],owner,decision)
        return decision

    def buffered_progress(self,owner,key,revision,**changes):
        # Merge with the latest book: never overwrite spent_fen committed by a fill.
        with self.locked(owner) as conn:
            cfg=self.paper._get(conn,'quant_config','config',owner,default_config())
            current=self.paper._get(conn,'quant_decision',key,owner,None)
            if cfg['mode']!='algorithm' or cfg['revision']!=revision or cfg['strategy_id']!=BUFFERED_PEG_ID or not current or current['status']!='running':
                raise PaperError('AUTOMATION_CHANGED','PEG B配置或决策已改变。')
            current.update(changes)
            self.paper._put(conn,'quant_decision',key,owner,current)
        return current

    def finish(self,owner,key,revision,status,results,reason=None,allocation_outcome=None):
        with self.locked(owner) as conn:
            decision=self.paper._get(conn,'quant_decision',key,owner,None)
            if decision and decision['revision']==revision:
                decision.update(status=status,results=results,reason=reason)
                if allocation_outcome is not None:decision['allocation_outcome']=allocation_outcome
                self.paper._put(conn,'quant_decision',key,owner,decision)

    def mark_peak(self,owner,revision,equity):
        with self.locked(owner) as conn:
            cfg=self.paper._get(conn,'quant_config','config',owner,default_config())
            if cfg['revision']!=revision or cfg['mode']!='algorithm':
                raise PaperError('AUTOMATION_CHANGED','托管配置已改变。')
            cfg['peak_equity_fen']=max(cfg['peak_equity_fen'],equity)
            if equity<cfg['peak_equity_fen']*.85:
                cfg.update(mode='manual',strategy_id=None,revision=revision+1,reason='净值较托管历史峰值回撤达到15%，已暂停，请检查持仓。')
            self.paper._put(conn,'quant_config','config',owner,cfg)
        return cfg

    def expire_peg_plans(self,owner,date):
        """Never replay yesterday's orders; preserve their committed fills."""
        with self.locked(owner) as conn:
            rows=conn.execute("SELECT record_key,payload FROM research_records WHERE namespace='quant_decision' AND owner=? AND record_key<?",(owner,date)).fetchall()
            for row in rows:
                decision=json.loads(row['payload'])
                if decision.get('strategy_id') in (PEG_STRATEGY_ID,BUFFERED_PEG_ID) and decision.get('status')=='running':
                    decision.update(status='expired',reason='上一交易日未完成的计划已过期；已成交记录保留，新一日根据实际持仓重新决策。')
                    self.paper._put(conn,'quant_decision',row['record_key'],owner,decision)


def authorize_execution(paper,conn,owner,account,intent,cached,execution,now,request_id=None):
    """Called AFTER replay lookup, INSIDE the money transaction and owner lock."""
    cfg=paper._get(conn,'quant_config','config',owner,default_config())
    if execution is None:
        if cfg['mode']=='algorithm': raise PaperError('AUTOMATION_ACTIVE','算法正在操作此账户，请先切回手动。')
        return {'actor':'manual'}
    if cfg['mode']!='algorithm' or cfg['revision']!=execution['revision'] or cfg['strategy_id']!=execution['strategy_id']:
        raise PaperError('AUTOMATION_CHANGED','托管已暂停或配置已改变，本轮订单停止。')
    suffix=' FOR UPDATE' if paper.db.dialect=='mysql' else ''
    row=conn.execute("SELECT payload FROM research_records WHERE namespace='lease' AND record_key=? AND owner=''"+suffix,('quant:'+owner,)).fetchone()
    lease=json.loads(row['payload']) if row else {}
    if lease.get('token')!=execution['lease_token'] or lease.get('expires',0)<=time.time():
        raise PaperError('QUANT_LEASE_LOST','执行租约已失效。')
    decision=paper._get(conn,'quant_decision',execution['decision_key'],owner,None)
    if not decision or decision['revision']!=cfg['revision'] or decision['model_version']!=execution['model_version'] or decision['date']!=now.date().isoformat() or decision['status']!='running':
        raise PaperError('AUTOMATION_CHANGED','决策已过期或结束。')
    is_buffered=cfg['strategy_id']==BUFFERED_PEG_ID
    is_peg=cfg['strategy_id'] in (PEG_STRATEGY_ID,BUFFERED_PEG_ID)
    if is_peg:
        planned=next((o for o in decision.get('orders',[]) if o['request_id']==request_id),None)
        if (decision.get('strategy_id')!=cfg['strategy_id'] or
                execution.get('peg_snapshot_id')!=decision.get('peg_snapshot',{}).get('snapshot_id') or
                cfg.get('strategy_config',{})!=decision.get('strategy_config',{}) or not planned or
                any(planned[k]!=intent[k] for k in ('code','side','quantity'))):
            raise PaperError('AUTOMATION_CHANGED','PEG配置、快照或订单与保存的决策不符。')
        if is_buffered:
            minute=now.hour*60+now.minute
            start,end=(602,606) if intent['side']=='sell' else (607,611)
            if not start<=minute<end:
                raise PaperError('PEG_WINDOW_CLOSED','已错过PEG B本轮执行窗口，等待下一交易日。')
    if intent['code'] not in decision['eligible']:
        raise PaperError('QUANT_RISK_LIMIT','股票未通过本轮行情和公司行为校验。')
    quote=valid_quote(cached,now)
    previous=decision['previous_close'].get(intent['code'])
    if not previous or abs(quote['price_fen']/100/previous-1)>=(price_change_limit(intent['code']) if is_peg else .045) or float(cached['candles'][-1]['volume'])<=0:
        raise PaperError('QUANT_PRICE_LIMIT','报价靠近涨跌停、停牌或价格基准异常，跳过。')
    quotes={c:paper._get(conn,'minute_market',c+':1','',{}) for c in account['positions']}
    for raw in quotes.values(): valid_quote(raw,now)
    valued=value_account(account,quotes,now);equity=valued['equity_fen'];gross=quote['price_fen']*intent['quantity']
    if not is_buffered and equity<cfg['peak_equity_fen']*.85:
        raise PaperError('QUANT_DRAWDOWN','回撤达到15%，暂停本轮自动交易。')
    day=paper._get(conn,'quant_day',decision['date'],owner,{'spent_fen':0,'budget_fen':int(equity*.3)})
    if not is_peg and (day['spent_fen']+gross>min(day['budget_fen'],int(equity*.3)) or decision['spent_fen']+gross>decision['budget_fen']):
        raise PaperError('QUANT_RISK_LIMIT','已达到每日30%调仓上限。')
    if intent['side']=='buy':
        position=account['positions'].get(intent['code'],{'quantity':0})
        if is_buffered:
            sectors=decision['sectors'];sector=sectors.get(intent['code'])
            peers=[c for c in account['positions'] if sectors.get(c)==sector]
            sector_value=sum(p['market_value_fen'] for p in valued['positions'] if sectors.get(p['code'])==sector)
            from ..paper_rules import fees
            if (not sector or any(c!=intent['code'] for c in peers) or
                    intent['code'] not in account['positions'] and len(account['positions'])>=3 or
                    sector_value+gross>(equity-fees(gross,'buy')['fees_fen'])*.6):
                raise PaperError('QUANT_RISK_LIMIT','PEG B要求最多3只、每一级行业1只且行业资金不超过60%。')
        if (not is_peg and (valued['market_value_fen']+gross>int(equity*.8) or position['quantity']*quote['price_fen']+gross>int(equity*.2))
                or intent['code'] not in account['positions'] and len(account['positions'])>=5):
            raise PaperError('QUANT_RISK_LIMIT','订单将超过5只持仓上限。' if is_peg else '订单将超过单票20%、总仓80%或5只持仓上限。')
    decision['spent_fen']+=gross;day['spent_fen']+=gross
    paper._put(conn,'quant_decision',execution['decision_key'],owner,decision)
    paper._put(conn,'quant_day',decision['date'],owner,day)
    return {'actor':'algorithm','strategy_id':cfg['strategy_id'],'model_version':execution['model_version'],'decision_key':execution['decision_key']}
