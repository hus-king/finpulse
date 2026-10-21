"""Automation state serialized by the same user row as paper money."""
import json
import math
import time
from contextlib import contextmanager

from ..auth import AuthError
from ..paper_rules import PaperError, new_account, valid_quote, value_account


def default_config():
    return {'mode':'manual','strategy_id':None,'revision':0,'peak_equity_fen':20_000_000,'reason':None}


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

    def configure(self,owner,mode,strategy_id,expected_revision,reason=None):
        with self.locked(owner) as conn:
            old=self.paper._get(conn,'quant_config','config',owner,default_config())
            if old['revision']!=expected_revision:
                raise PaperError('AUTOMATION_CONFLICT','操作方式已变化，请刷新后再试。')
            new={**old,'mode':mode,'strategy_id':strategy_id if mode=='algorithm' else None,
                 'revision':old['revision']+1,'reason':reason,'updated_at':int(time.time())}
            self.paper._put(conn,'quant_config','config',owner,new)
        return new

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
            self.paper._put(conn,'quant_decision',decision['key'],owner,decision)
        return decision

    def finish(self,owner,key,revision,status,results,reason=None):
        with self.locked(owner) as conn:
            decision=self.paper._get(conn,'quant_decision',key,owner,None)
            if decision and decision['revision']==revision:
                decision.update(status=status,results=results,reason=reason)
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


def authorize_execution(paper,conn,owner,account,intent,cached,execution,now):
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
    if intent['code'] not in decision['eligible']:
        raise PaperError('QUANT_RISK_LIMIT','股票未通过本轮行情和公司行为校验。')
    quote=valid_quote(cached,now)
    previous=decision['previous_close'].get(intent['code'])
    if not previous or abs(quote['price_fen']/100/previous-1)>=.045 or float(cached['candles'][-1]['volume'])<=0:
        raise PaperError('QUANT_PRICE_LIMIT','报价靠近涨跌停、停牌或价格基准异常，跳过。')
    quotes={c:paper._get(conn,'minute_market',c+':1','',{}) for c in account['positions']}
    for raw in quotes.values(): valid_quote(raw,now)
    valued=value_account(account,quotes,now);equity=valued['equity_fen'];gross=quote['price_fen']*intent['quantity']
    if equity<cfg['peak_equity_fen']*.85:
        raise PaperError('QUANT_DRAWDOWN','回撤达到15%，暂停本轮自动交易。')
    day=paper._get(conn,'quant_day',decision['date'],owner,{'spent_fen':0,'budget_fen':int(equity*.3)})
    if day['spent_fen']+gross>min(day['budget_fen'],int(equity*.3)) or decision['spent_fen']+gross>decision['budget_fen']:
        raise PaperError('QUANT_RISK_LIMIT','已达到每日30%调仓上限。')
    if intent['side']=='buy':
        position=account['positions'].get(intent['code'],{'quantity':0})
        if (valued['market_value_fen']+gross>int(equity*.8) or position['quantity']*quote['price_fen']+gross>int(equity*.2)
                or intent['code'] not in account['positions'] and len(account['positions'])>=5):
            raise PaperError('QUANT_RISK_LIMIT','订单将超过单票20%、总仓80%或5只持仓上限。')
    decision['spent_fen']+=gross;day['spent_fen']+=gross
    paper._put(conn,'quant_decision',execution['decision_key'],owner,decision)
    paper._put(conn,'quant_day',decision['date'],owner,day)
    return {'actor':'algorithm','strategy_id':cfg['strategy_id'],'model_version':execution['model_version'],'decision_key':execution['decision_key']}
