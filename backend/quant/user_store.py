"""Private JSON strategies, CAS and quotas under the existing account lock."""
import time
import uuid

from ..paper_rules import PaperError
from .rules import MAX_STRATEGIES, normalize_package, RuleStrategy


class UserStrategyStore:
    def __init__(self,quant_store):
        self.base=quant_store;self.paper=quant_store.paper;self.records=quant_store.records

    def consume_write(self,owner):
        minute=int(time.time())//60
        with self.base.locked(owner) as conn:
            state=self.paper._get(conn,'user_strategy_rate','rate',owner,{'minute':minute,'count':0})
            if state['minute']!=minute:state={'minute':minute,'count':0}
            if state['count']>=20:raise PaperError('STRATEGY_RATE_LIMIT','策略写入与校验每分钟最多20次，请稍后重试。',429)
            state['count']+=1;self.paper._put(conn,'user_strategy_rate','rate',owner,state)

    def list(self,owner):
        return [row['value'] for row in self.records.list('user_strategy',owner,limit=MAX_STRATEGIES)]

    def get(self,owner,strategy_id):
        result=self.records.get('user_strategy',strategy_id,owner,default=None)
        if not result:raise PaperError('STRATEGY_NOT_FOUND','未找到你的策略。',404)
        return result

    def adapter(self,owner,strategy_id):
        return self.from_record(self.get(owner,strategy_id))

    @staticmethod
    def from_record(record):
        try:
            normalized,digest=normalize_package(record['strategy'])
            if digest!=record['digest'] or type(record['revision']) is not int or record['revision']<1:raise ValueError('checksum/revision')
            return RuleStrategy(normalized,record['id'],record['revision'],digest)
        except (ValueError,TypeError,KeyError):raise PaperError('STRATEGY_INVALID','保存的策略未通过校验，请重新编辑保存。') from None

    def metadata(self,owner):
        items=[]
        for record in self.list(owner):
            try:items.append(self.from_record(record).metadata())
            except PaperError:
                items.append({'id':record.get('id'),'name':str(record.get('strategy',{}).get('name','我的策略'))[:60],
                              'origin':'user','available':False,'revision':record.get('revision'),'reason':'保存的策略未通过校验'})
        return items

    def create(self,owner,package):
        normalized,digest=normalize_package(package)
        with self.base.locked(owner) as conn:
            count=conn.execute('SELECT COUNT(*) AS total FROM research_records WHERE namespace=? AND owner=?',('user_strategy',owner)).fetchone()['total']
            if count>=MAX_STRATEGIES:raise PaperError('STRATEGY_QUOTA','每个账号最多保存10份策略，请先删除不再使用的策略。')
            record={'id':'user-'+uuid.uuid4().hex,'revision':1,'digest':digest,'strategy':normalized,
                    'created_at':int(time.time()),'updated_at':int(time.time())}
            self.paper._put(conn,'user_strategy',record['id'],owner,record)
        return record

    def _editable(self,conn,owner,strategy_id,expected_revision):
        record=self.paper._get(conn,'user_strategy',strategy_id,owner,None)
        if not record:raise PaperError('STRATEGY_NOT_FOUND','未找到你的策略。',404)
        if type(expected_revision) is not int or record['revision']!=expected_revision:
            raise PaperError('STRATEGY_REVISION_CONFLICT','策略已更新，请重新读取后再修改。')
        cfg=self.paper._get(conn,'quant_config','config',owner,{})
        if cfg.get('mode')=='algorithm' and cfg.get('strategy_id')==strategy_id:
            raise PaperError('STRATEGY_IN_USE','此策略正在托管，请先暂停并切回手动再修改或删除。')
        return record

    def update(self,owner,strategy_id,package,expected_revision):
        normalized,digest=normalize_package(package)
        with self.base.locked(owner) as conn:
            old=self._editable(conn,owner,strategy_id,expected_revision)
            record={**old,'revision':old['revision']+1,'strategy':normalized,'digest':digest,'updated_at':int(time.time())}
            self.paper._put(conn,'user_strategy',strategy_id,owner,record)
        return record

    def delete(self,owner,strategy_id,expected_revision):
        with self.base.locked(owner) as conn:
            self._editable(conn,owner,strategy_id,expected_revision)
            conn.execute('DELETE FROM research_records WHERE namespace=? AND record_key=? AND owner=?',('user_strategy',strategy_id,owner))
        return {'deleted':True,'id':strategy_id}
