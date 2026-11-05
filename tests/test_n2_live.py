import asyncio
import copy
from datetime import date, datetime, timedelta
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
import uuid
import sys

from backend.auth import AuthStore
from backend.news_cleaning import SHANGHAI
from backend.paper_rules import PaperError, apply_trade, new_account, valid_quote
from backend.paper_store import PaperStore
from backend.paper_trading import PaperTradingService
from backend.research_store import ResearchStore
from backend.quant.n2_book import digest, model_fees
from backend.quant.n2_live import N2InputService, N2Strategy, commit_bar, settle_owner_cash
from backend.quant.n2_policy import N2_STRATEGY_ID
from backend.quant.service import QuantService


DAY = '2026-10-09'
CODES = ['000001', '600000', '600030']


def sample_inputs():
    from backend.trading_calendar import is_trade_day
    cursor = date(2025, 1, 1)
    days = []
    while cursor <= date(2026, 10, 8):
        if cursor.weekday() < 5 and is_trade_day(cursor) is not False:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    histories = {}
    for code in CODES:
        bars = [dict(date=d, open=10., high=10., low=10., close=10., volume=100000) for d in days]
        histories[code] = dict(raw=bars, adjusted=copy.deepcopy(bars))
    rows = [dict(code=code, name='股票'+code, peg=(i+1)/10, industry_l1='行业'+str(i), status='合格') for i, code in enumerate(CODES)]
    coverage = [dict(code=code, status='合格') for code in CODES]
    coverage += [dict(code=f'002{i:03}', status='观察') for i in range(297)]
    return dict(date=DAY, signal_date='2026-10-08', calendar=days, rows=rows, histories=histories,
                sectors={r['code']:r['industry_l1'] for r in rows}, coverage=coverage,
                provenance={'fixture':'past-only'}, daily={})


class N2LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'FINPULSE_N2_DATA_DIR':str(self.directory/'managed')})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)
        self.auth = AuthStore(self.directory/'book.db')
        self.auth.initialize()
        self.owner = 'owner-one'
        with self.auth.transaction() as conn:
            conn.execute('INSERT INTO users(id,username,nickname,password_hash,is_active,created_at) VALUES(?,?,?,?,?,?)',
                         (self.owner, 'n2-test', '测试', 'unused', 1, int(time.time())))
        self.records = ResearchStore(self.auth)
        self.records.initialize()
        self.now = datetime(2026, 10, 9, 10, tzinfo=SHANGHAI)
        catalog = SimpleNamespace(get=lambda code:dict(code=code, name='股票'+code), ensure=AsyncMock())
        paper = PaperTradingService(PaperStore(self.records), catalog, SimpleNamespace(tasks={}), clock=lambda:self.now)
        self.service = QuantService(paper, enabled=False, clock=lambda:self.now)
        self.fresh_codes = []
        async def fresh(code):
            self.fresh_codes.append(code)
            value = self.records.get('minute_market', code+':1', default={})
            valid_quote(value, self.now)
            return value
        self.service.fresh = fresh
        self.inputs = sample_inputs()
        self.publish_inputs()
        self.publish_quotes()

    def publish_inputs(self, **changes):
        self.inputs.update(changes)
        root = self.directory/'managed'
        root.mkdir(exist_ok=True)
        path = root/'inputs.json'
        path.write_text(json.dumps(self.inputs))
        import hashlib
        meta = dict(date=DAY, signal_date=self.inputs['signal_date'], path=str(path),
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(), prepared_at=self.now.isoformat())
        self.records.put('n2_inputs', 'latest', meta)
        return meta

    def publish_quotes(self, *, minute=None, volume=12345, high=10.02, low=9.98, codes=None):
        if minute is not None:
            self.now = self.now.replace(hour=10, minute=minute, second=30)
        maximum = self.now.hour*60+self.now.minute
        for code in codes or self.inputs['histories']:
            bars = []
            for i in range(600, maximum+1):
                end = self.now.replace(hour=i//60, minute=i%60, second=0, microsecond=0)
                bars.append(dict(date=end.isoformat(), open=10., close=10., high=high if i>600 else 10.,
                                 low=low if i>600 else 10., volume=volume))
            self.records.put('minute_market', code+':1', dict(candles=bars, fetched_at=self.now.isoformat(),
                                                             volume_unit='shares', error=None))

    def enable(self):
        return self.service.configure(self.owner, 'algorithm', N2_STRATEGY_ID, 0)

    def tick(self):
        asyncio.run(self.service.tick())

    def decision(self):
        return self.service.store.decisions(self.owner)['items'][0]

    def account(self):
        return self.service.paper.store.get_account(self.owner)

    def ready_buy(self):
        cfg = self.enable()
        self.tick()
        self.publish_quotes(minute=7)
        self.tick()
        return cfg

    def holding(self, code=CODES[0], owner=None):
        owner = owner or self.owner
        when = datetime(2026,10,8,10,tzinfo=SHANGHAI)
        book, trade = apply_trade(new_account(),dict(code=code,name='登记持仓'),'buy',100,
                                 dict(price_fen=1000,as_of=when.isoformat(),fetched_at=when.isoformat(),source='fixture'),when)
        book['sequence']=1
        trade.update(sequence=1,request_id=str(uuid.uuid4()))
        self.records.put('paper_account','account',book,owner)
        self.records.put('paper_trade',trade['request_id'],trade,owner)
        return book

    def dividend(self, **changes):
        return dict(dict(div_proc='实施',ann_date='20261001',end_date='20260630',
                         record_date='20261008',ex_date='20261009',pay_date='20261012',
                         cash_div_tax=.5,stk_div=0),**changes)

    def committed_bar(self, cfg, token, *, changed=None):
        plan = self.decision()
        order = next(o for o in plan['orders'] if o['side']=='buy')
        raw = self.records.get('minute_market', order['code']+':1')
        bar = next(b for b in raw['candles'] if b['date'][11:16]=='10:08')
        wanted = digest(bar)
        if changed:
            changed(raw, bar)
            self.records.put('minute_market', order['code']+':1', raw)
        return commit_bar(self.service, self.owner, cfg, plan['key'], token,
                          order['id'], order['code'], bar['date'], wanted)

    def test_fixed_registry_and_dated_artifact(self):
        metadata = self.service.strategies(self.owner)
        self.assertTrue(any(s['id']==N2_STRATEGY_ID and s['name']=='四因子 · 双股倒波动' for s in metadata))
        self.assertTrue(any(s['id']=='official-peg-buffered-v1' for s in metadata))
        for config in [{'top_n':2}, None, False]:
            with self.assertRaises(ValueError):N2Strategy().validate_config(config)
        value, meta = self.service.n2_inputs.load(DAY)
        self.assertEqual(value['signal_date'], '2026-10-08')
        self.records.put('n2_inputs', 'latest', {**meta,'sha256':'0'*64})
        with self.assertRaises(ValueError):self.service.n2_inputs.load(DAY)

    def test_late_activation_skips_without_latching_or_fabricating_past_orders(self):
        self.now = self.now.replace(hour=11)
        self.enable()
        with patch('backend.quant.n2_live._latch_day', side_effect=AssertionError('late plan latch')):
            self.tick()
        plan = self.decision()
        self.assertEqual(plan['status'], 'completed')
        self.assertEqual(plan['execution_mode'], 'live_minute')
        self.assertEqual(plan['orders'], [])
        self.assertEqual(self.account()['sequence'], 0)

    def test_cash_ex_is_settled_before_open_once_without_quotes_or_sequence_changes(self):
        before=self.holding();self.enable()
        future=copy.deepcopy(self.records.list('paper_trade',self.owner)[0]['value'])
        future.update(sequence=999,request_id=str(uuid.uuid4()),quantity=10000)
        self.records.put('paper_trade',future['request_id'],future,self.owner)
        self.publish_inputs(dividends={CODES[0]:[self.dividend()]})
        self.now=self.now.replace(hour=8,minute=59)
        self.tick()
        self.assertEqual(self.account().get('receivable_fen',0),0)
        self.now=self.now.replace(hour=9,minute=0)
        self.tick();self.tick()
        book=self.account();events=self.records.list('paper_corporate',self.owner)
        self.assertEqual(book['cash_fen'],before['cash_fen'])
        self.assertEqual((book['receivable_fen'],book['corporate_revision'],book['sequence']),(4000,1,1))
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['value']['type'],'dividend_ex')
        self.assertEqual(self.fresh_codes,[])
        self.assertEqual(self.service.store.decisions(self.owner)['items'],[])

    def test_late_cash_posting_follows_the_last_trade_without_backdating_cash(self):
        from backend.quant.n2_corporate import settle_cash_actions
        from backend.quant.n2_book import seed_state, update_manual_state
        before=self.holding()
        trades=[r['value'] for r in self.records.list('paper_trade',self.owner)]
        account,state,_=settle_cash_actions(before,seed_state(trades,before),trades,
                                             {CODES[0]:[self.dividend()]},self.now.replace(hour=9))
        when=self.now.replace(day=13,hour=10)
        sale_before=account
        account,sold=apply_trade(account,dict(code=CODES[0],name='登记持仓'),'sell',100,
                                 dict(price_fen=1000,as_of=when.isoformat(),fetched_at=when.isoformat(),source='fixture'),when)
        account['sequence']=2;sold.update(sequence=2)
        update_manual_state(state,sale_before,account,sold)
        trades.append(sold)
        updated,_,events=settle_cash_actions(account,state,trades,{},when.replace(minute=1))
        self.assertEqual(len(events),1)
        event=events[0]
        self.assertGreater(datetime.fromisoformat(event['effective_at']),when)
        self.assertEqual(event['scheduled_at'],'2026-10-12T15:00:00+08:00')
        self.assertEqual(updated['cash_fen'],account['cash_fen']+4000)

    def test_morning_plan_equity_includes_ex_receivable(self):
        before=self.holding();self.enable()
        self.publish_inputs(dividends={CODES[0]:[self.dividend()]})
        self.tick()
        self.assertEqual(self.decision()['decision_equity_fen'],before['cash_fen']+100*1000+4000)
        self.assertEqual(self.account()['sequence'],1)

    def test_registered_cash_pays_after_liquidation_and_pause_after_close(self):
        self.holding();cfg=self.enable()
        self.publish_inputs(dividends={CODES[0]:[self.dividend()]})
        self.now=self.now.replace(hour=9)
        self.tick()
        self.now=self.now.replace(hour=10)
        self.service.configure(self.owner,'manual',None,cfg['revision'])
        intent=dict(code=CODES[0],side='sell',quantity=100)
        def apply(book,cached):
            return apply_trade(book,dict(code=CODES[0],name='登记持仓'),'sell',100,valid_quote(cached,self.now),self.now)
        self.service.paper.store.transact(self.owner,str(uuid.uuid4()),intent,apply,now=lambda:self.now)
        self.assertEqual(self.account()['positions'],{})
        before=self.account()
        # Existing claims remain payable with no active collector or artifact.
        with self.auth.transaction() as conn:
            conn.execute("DELETE FROM research_records WHERE namespace='n2_inputs'")
        self.now=self.now.replace(day=12,hour=14,minute=59)
        self.tick()
        self.assertEqual(self.account(),before)
        self.now=self.now.replace(hour=15,minute=0)
        self.tick();self.tick()
        book=self.account()
        self.assertEqual(book['cash_fen'],before['cash_fen']+4000)
        self.assertEqual(book['realized_pnl_fen'],before['realized_pnl_fen']+4000)
        self.assertEqual((book['receivable_fen'],book['sequence'],book['corporate_revision']),(0,2,2))
        self.assertEqual(len(self.records.list('paper_corporate',self.owner)),2)

    def test_cash_event_and_claim_rollback_and_configuration_guard(self):
        before=self.holding();cfg=self.enable()
        self.publish_inputs(dividends={CODES[0]:[self.dividend()]})
        self.now=self.now.replace(hour=9)
        original=self.service.paper.store._put
        def fail(conn,namespace,key,owner,value):
            if namespace=='paper_account':raise RuntimeError('injected cash write failure')
            return original(conn,namespace,key,owner,value)
        with patch.object(self.service.paper.store,'_put',side_effect=fail),self.assertLogs('backend.quant.n2_live',level='ERROR'):
            self.tick()
        self.assertEqual(self.account(),before)
        self.assertEqual(self.records.list('paper_corporate',self.owner),[])
        self.assertEqual(self.records.get('n2_state','state',self.owner).get('cash_claims',{}),{})
        self.assertEqual(self.service.state(self.owner)['runtime']['component'],'n2_cash')
        self.service.configure(self.owner,'manual',None,cfg['revision'])
        with self.assertRaises(PaperError):settle_owner_cash(self.service,self.owner,cfg,self.now,self.inputs)
        self.tick()
        self.assertEqual(self.account()['receivable_fen'],4000)
        self.assertEqual(self.service.state(self.owner)['runtime']['status'],'ok')

    def test_cash_failure_does_not_block_another_owner(self):
        other='owner-two'
        with self.auth.transaction() as conn:
            conn.execute('INSERT INTO users(id,username,nickname,password_hash,is_active,created_at) VALUES(?,?,?,?,?,?)',
                         (other,'n2-second','第二账户','unused',1,int(time.time())))
        self.holding();self.enable()
        self.holding(CODES[1],other)
        self.service.configure(other,'algorithm',N2_STRATEGY_ID,0)
        self.publish_inputs(dividends={CODES[0]:[self.dividend(stk_div=.1)],CODES[1]:[self.dividend()]})
        self.tick()
        self.assertEqual(self.service.state(self.owner)['runtime']['status'],'error')
        self.assertEqual(self.service.store.decisions(self.owner)['items'],[])
        self.assertEqual(self.service.paper.store.get_account(other)['receivable_fen'],4000)
        self.assertEqual(len(self.service.store.decisions(other)['items']),1)

    def test_cash_inputs_ignore_future_announcements_and_unfinished_record_day(self):
        self.holding();self.enable()
        self.publish_inputs(dividends={CODES[0]:[self.dividend(ann_date='20261010')]})
        self.now=self.now.replace(hour=9)
        self.tick()
        self.assertEqual(self.records.list('paper_corporate',self.owner),[])
        self.publish_inputs(dividends={CODES[0]:[self.dividend(record_date='20261009',ex_date='20261012',pay_date='20261013')]})
        self.now=self.now.replace(hour=14)
        self.tick()
        self.assertEqual(self.records.get('n2_state','state',self.owner).get('cash_claims',{}),{})
        meta=self.records.get('n2_inputs','latest');meta['date']='2026-10-12'
        self.records.put('n2_inputs','latest',meta)
        self.tick()
        self.assertEqual(self.service.state(self.owner)['runtime']['status'],'error')

    def test_on_time_plan_fetches_only_two_targets_and_fills_closed_minutes_at_one_percent(self):
        self.ready_buy()
        self.assertEqual(set(self.fresh_codes), set(CODES[:2]))
        self.assertEqual(self.account()['sequence'], 0)
        for minute in range(8, 13):
            self.publish_quotes(minute=minute)
            self.tick()
        account, plan = self.account(), self.decision()
        self.assertEqual(plan['status'], 'completed')
        self.assertEqual(set(account['positions']), set(CODES[:2]))
        self.assertTrue(all(p['quantity']==4*123 for p in account['positions'].values()))
        trades = self.service.paper.trades(self.owner, limit=100)['items']
        self.assertEqual(len(trades), 8)
        self.assertTrue(all(t['price_fen']==1002 and t['quantity']==123 for t in trades))
        self.assertTrue(all(t['executed_at'][11:16] in ['10:08','10:09','10:10','10:11'] for t in trades))
        self.assertTrue(all(t['processed_at']>=t['executed_at'] for t in trades))
        for order in plan['orders']:
            family = [t for t in trades if t['parent_order_id']==order['id']]
            self.assertEqual(sum(t['fees_fen'] for t in family), model_fees(sum(t['gross_fen'] for t in family),'buy')['fees_fen'])
        sequence = account['sequence']
        self.tick()
        self.assertEqual(self.account()['sequence'], sequence)
        state = self.records.get('n2_state','state',self.owner)
        self.assertEqual(state['sequence'], sequence)
        self.assertTrue(all(e['initial_quantity']==492 and e['price_fen']==1002 for e in state['entries'].values()))

    def test_uuid_replay_returns_committed_fill_even_after_pause_without_new_quote(self):
        cfg = self.ready_buy()
        self.publish_quotes(minute=8)
        token = self.records.claim('quant:'+self.owner,300)
        first = self.committed_bar(cfg,token)
        self.service.configure(self.owner,'manual',None,cfg['revision'])
        self.records.release('quant:'+self.owner,token)
        self.records.put('minute_market',first['code']+':1',{})
        second = self.committed_bar_from_trade(cfg,first)
        self.assertEqual(second,first)
        self.assertEqual(self.account()['sequence'],1)

    def committed_bar_from_trade(self,cfg,trade):
        return commit_bar(self.service,self.owner,cfg,trade['decision_key'],'obsolete',trade['parent_order_id'],
                          trade['code'],trade['bar_end'],'ignored-after-success')

    def test_lost_lease_changed_revision_and_raw_bar_revision_cannot_mutate_money(self):
        cfg = self.ready_buy()
        self.publish_quotes(minute=8)
        token = self.records.claim('quant:'+self.owner,300)
        for given, expected in [('wrong','QUANT_LEASE_LOST')]:
            with self.assertRaises(PaperError) as result:self.committed_bar(cfg,given)
            self.assertEqual(result.exception.code,expected)
        with self.assertRaises(PaperError) as result:
            self.committed_bar(cfg,token,changed=lambda raw,b:b.update(high=10.03))
        self.assertEqual(result.exception.code,'QUOTE_CHANGED')
        self.assertEqual(self.account()['sequence'],0)
        self.service.configure(self.owner,'manual',None,cfg['revision'])
        with self.assertRaises(PaperError) as result:self.committed_bar(cfg,token)
        self.assertEqual(result.exception.code,'AUTOMATION_CHANGED')
        self.assertEqual(self.account()['sequence'],0)

    def test_unclosed_stale_zero_volume_and_pre_submission_bars_are_blocked(self):
        cfg = self.ready_buy()
        self.publish_quotes(minute=8)
        token = self.records.claim('quant:'+self.owner,300)
        original = self.records.get('minute_market',CODES[0]+':1')
        self.now = self.now.replace(minute=7,second=30)
        forming = copy.deepcopy(original);forming['fetched_at']=self.now.isoformat()
        self.records.put('minute_market',CODES[0]+':1',forming)
        with self.assertRaises(PaperError) as result:self.committed_bar(cfg,token)
        self.assertEqual(result.exception.code,'N2_WINDOW_CLOSED')
        self.now = self.now.replace(minute=8,second=30)
        stale = copy.deepcopy(original);stale['fetched_at']=(self.now-timedelta(seconds=91)).isoformat()
        self.records.put('minute_market',CODES[0]+':1',stale)
        with self.assertRaises(PaperError) as result:self.committed_bar(cfg,token)
        self.assertEqual(result.exception.code,'QUOTE_STALE')
        self.records.put('minute_market',CODES[0]+':1',copy.deepcopy(original))
        zero = self.records.get('minute_market',CODES[0]+':1')
        next(b for b in zero['candles'] if b['date'][11:16]=='10:08')['volume']=0
        self.records.put('minute_market',CODES[0]+':1',zero)
        with self.assertRaises(PaperError) as result:self.committed_bar(cfg,token)
        self.assertEqual(result.exception.code,'QUOTE_UNAVAILABLE')
        self.records.put('minute_market',CODES[0]+':1',original)
        plan = self.decision();plan['created_at']=self.now.isoformat();self.records.put('quant_decision',plan['key'],plan,self.owner)
        with self.assertRaises(PaperError) as result:self.committed_bar(cfg,token)
        self.assertEqual(result.exception.code,'N2_WINDOW_CLOSED')
        self.assertEqual(self.account()['sequence'],0)

    def test_valid_stop_and_absence_are_latched_once_before_quote_failure(self):
        code='600999'
        self.inputs['histories'][code]=copy.deepcopy(self.inputs['histories'][CODES[0]])
        self.inputs['sectors'][code]='持仓行业'
        for kind in ['raw','adjusted']:
            for offset,price in [(-3,9.2),(-2,8.5),(-1,8.)]:
                self.inputs['histories'][code][kind][offset].update(open=price,high=price,low=price,close=price)
        self.publish_inputs()
        when=datetime(2026,9,28,10,tzinfo=SHANGHAI)
        book,trade=apply_trade(new_account(),dict(code=code,name='历史持仓'),'buy',100,
                               dict(price_fen=1000,as_of=when.isoformat(),fetched_at=when.isoformat(),source='fixture'),when)
        book['sequence']=1;trade.update(sequence=1,request_id=str(uuid.uuid4()),intent=dict(code=code,side='buy',quantity=100))
        self.records.put('paper_account','account',book,self.owner);self.records.put('paper_trade',trade['request_id'],trade,self.owner)
        cfg=self.enable()
        self.service.fresh=AsyncMock(side_effect=PaperError('QUOTE_UNAVAILABLE','fixture quote unavailable'))
        for _ in range(2):
            token=self.records.claim('quant:'+self.owner,300)
            from backend.quant.n2_live import run_n2
            with self.assertRaises(PaperError):asyncio.run(run_n2(self.service,self.owner,cfg,DAY+':'+str(cfg['revision']).zfill(10),token))
            self.records.release('quant:'+self.owner,token)
            state=self.records.get('n2_state','state',self.owner)
            self.assertEqual(state['pending'][code],'price_hard')
            self.assertEqual(state['streaks'][code],1)
        self.assertEqual(self.account()['sequence'],1)

    def test_failed_fill_write_rolls_back_account_order_and_anchor_together(self):
        cfg=self.ready_buy();self.publish_quotes(minute=8)
        token=self.records.claim('quant:'+self.owner,300)
        paper=self.service.paper.store;original=paper._put
        before=(self.account(),self.decision(),self.records.get('n2_state','state',self.owner))
        def fail(conn,namespace,key,owner,payload):
            if namespace=='paper_trade':raise RuntimeError('injected fill-write failure')
            return original(conn,namespace,key,owner,payload)
        with patch.object(paper,'_put',side_effect=fail):
            with self.assertRaises(RuntimeError):self.committed_bar(cfg,token)
        after=(self.account(),self.decision(),self.records.get('n2_state','state',self.owner))
        self.assertEqual(before,after)
        self.assertEqual(self.records.list('paper_trade',self.owner),[])
        self.assertEqual(self.committed_bar(cfg,token)['sequence'],1)

    def test_manual_committed_trade_updates_existing_n2_anchor_and_sequence(self):
        cfg=self.enable();self.service.configure(self.owner,'manual',None,cfg['revision'])
        intent=dict(code=CODES[0],side='buy',quantity=100)
        def apply(book,cached):
            return apply_trade(book,dict(code=CODES[0],name='手动股票'),'buy',100,valid_quote(cached,self.now),self.now)
        self.service.paper.store.transact(self.owner,str(uuid.uuid4()),intent,apply,now=lambda:self.now)
        state=self.records.get('n2_state','state',self.owner)
        self.assertEqual(state['sequence'],1)
        self.assertEqual(state['entries'][CODES[0]]['price_fen'],1000)
        self.service.configure(self.owner,'algorithm',N2_STRATEGY_ID,2)
        self.records.put('paper_replay','active',{'start_date':'2026-10-08','execution_mode':'daily-open-approximation'},self.owner)
        self.assertEqual(self.service.state(self.owner)['replay']['start_date'],'2026-10-08')

    def test_preparation_timing_and_stale_attempt_cannot_replace_newer_job(self):
        manager=self.service.n2_inputs
        self.assertEqual(manager.desired_day(self.now.replace(hour=9)),DAY)
        for hour,minute in [(10,0),(15,30),(21,4)]:
            self.assertIsNone(manager.desired_day(self.now.replace(hour=hour,minute=minute)))
        self.assertIsNone(manager.desired_day(self.now.replace(hour=21,minute=5)))
        self.assertIsNone(manager.desired_day(self.now.replace(day=10,hour=12)))
        self.assertIsNone(manager.desired_day(self.now.replace(day=11,hour=12)))
        self.assertEqual(manager.desired_day(self.now.replace(day=11,hour=21,minute=5)),'2026-10-12')
        self.assertEqual(manager.desired_day(self.now.replace(day=7,hour=21,minute=5)),'2026-10-08')
        self.assertIsNone(manager.desired_day(self.now.replace(year=2027)))
        self.records.put('n2_inputs','attempt',{'id':'new','status':'running'})
        manager._finish_attempt({'id':'old','status':'failed'})
        self.assertEqual(self.records.get('n2_inputs','attempt')['id'],'new')
        token=self.records.claim('n2-daily-global',180)
        meta=self.records.get('n2_inputs','latest')
        with self.assertRaises(ValueError):manager._publish({'id':'old','status':'complete'},meta,'wrong')
        manager._publish({'id':'good','status':'complete'},meta,token)
        self.assertEqual(self.records.get('n2_inputs',DAY)['sha256'],meta['sha256'])

    def test_cancelled_preparation_terminates_only_its_disposable_child_and_releases_lease(self):
        manager=self.service.n2_inputs
        original=asyncio.create_subprocess_exec
        children=[]
        async def harmless_child(*argv,**kwargs):
            # Exercise actual ownership/termination, without running a provider.
            child=await original(sys.executable,'-c','import time; time.sleep(60)',**kwargs)
            children.append(child)
            return child
        async def scenario():
            with patch.dict(os.environ,{'FINPULSE_N2_BASE_DIR':str(self.directory/'base'),
                                       'FINPULSE_N2_PROVIDER_CONFIG':str(self.directory/'private.json')}), \
                 patch('backend.quant.n2_live.asyncio.create_subprocess_exec',side_effect=harmless_child):
                manager.task=asyncio.create_task(manager._run('2026-10-12',[]))
                for _ in range(100):
                    if children:break
                    await asyncio.sleep(.01)
                self.assertEqual(len(children),1)
                await manager.close()
        asyncio.run(scenario())
        self.assertIsNotNone(children[0].returncode)
        self.assertEqual(self.records.get('n2_inputs','attempt')['status'],'interrupted')
        self.assertIsNone(self.records.get('lease','n2-daily-global'))

    def test_refresh_worker_queries_calendar_yesterday_for_weekend_reports(self):
        path=Path(__file__).resolve().parents[1]/'scripts/quant/refresh_n2.py'
        spec=importlib.util.spec_from_file_location('refresh_n2_test',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        config=self.directory/'private.json';config.write_text('{}')
        artifact=self.directory/'generated.json'
        value={**self.inputs,'date':'2026-10-12','signal_date':'2026-10-09'}
        argv=[str(path),'--base',str(self.directory/'base'),'--overlay',str(self.directory/'overlay'),
              '--config',str(config),'--decision-day','2026-10-12','--artifact',str(artifact)]
        with patch.object(sys,'argv',argv),patch.object(module.resource,'setrlimit'), \
             patch('backend.quant.n2_inputs.refresh_archive') as refresh, \
             patch('backend.quant.n2_inputs.load_inputs',return_value=value),patch('sys.stdout',new_callable=io.StringIO):
            module.main()
        self.assertEqual(refresh.call_args.args[3],'2026-10-11')
        self.assertEqual(json.loads(artifact.read_text())['signal_date'],'2026-10-09')


if __name__=='__main__':
    unittest.main()
