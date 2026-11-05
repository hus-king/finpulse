import copy
from datetime import datetime
import unittest

from backend.news_cleaning import SHANGHAI
from backend.paper_rules import new_account, daily_performance
from backend.quant.n2_corporate import settle_cash_actions
from backend.quant.n2_policy import initial_state
from backend.quant.n2_replay import replay
from backend.quant.n2_maintenance import verify_replay
from backend.quant.n2_book import digest
from test_n2_replay import inputs, NOW


EVENT = dict(div_proc='实施', ann_date='20260905', end_date='20260630', record_date='20261008',
             ex_date='20261009', pay_date='20261012', stk_div=0, cash_div_tax=.265)


class CashDividendTests(unittest.TestCase):
    def test_record_entitlement_survives_sale_and_cash_waits_for_pay_close(self):
        account=new_account();state=initial_state()
        trades=[dict(code='600000',side='buy',quantity=100,executed_at='2026-10-08T09:30:00+08:00')]
        def settle(day, hour):
            nonlocal account,state
            account,state,events=settle_cash_actions(account,state,trades,{'600000':[EVENT]},
                datetime.fromisoformat(day+f'T{hour}:00:00+08:00'))
            return events
        self.assertEqual(settle('2026-10-08','14'),[])
        ex=settle('2026-10-09','09')
        self.assertEqual(ex[0]['quantity_entitled'],100)
        self.assertEqual(account['receivable_fen'],2120)
        self.assertEqual(account['cash_fen'],20_000_000)
        trades.append(dict(code='600000',side='sell',quantity=100,executed_at='2026-10-09T10:00:00+08:00'))
        self.assertEqual(settle('2026-10-12','10'),[])
        pay=settle('2026-10-12','15')
        self.assertEqual(pay[0]['income_fen'],2120)
        self.assertEqual(account['cash_fen'],20_002_120)
        self.assertEqual(account['receivable_fen'],0)
        self.assertEqual(settle('2026-10-13','15'),[])
        self.assertEqual(account['corporate_revision'],2)

    def test_replay_cash_events_rebuild_and_tampering_is_rejected(self):
        def loader(*a,**kw):
            data=inputs(*a,**kw);data['dividends']={'600000':[EVENT]};return data
        value=replay('','','2026-10-08','2026-10-12','dividend-fixture',input_loader=loader)
        self.assertEqual(len(value['corporate_events']),2)
        self.assertGreater(value['account']['realized_pnl_fen'],0)
        self.assertEqual(verify_replay(value,now=NOW),value['account'])
        bad=copy.deepcopy(value);bad['corporate_events'][1]['income_fen']+=1
        bad['sha256']=digest({k:v for k,v in bad.items() if k!='sha256'})
        with self.assertRaisesRegex(ValueError,'分红权益'):verify_replay(bad,now=NOW)

    def test_ex_date_daily_pnl_credits_only_once(self):
        account=new_account();account.update(receivable_fen=2120)
        account['positions']={'600000':dict(quantity=100)}
        valuation=dict(equity_fen=20_000_000+100*974+2120,
                       positions=[dict(code='600000',market_value_fen=97400)])
        event=dict(code='600000',cash_delta_fen=0,receivable_delta_fen=2120)
        result=daily_performance(account,[],{'600000':1000},valuation,[event])
        self.assertEqual(result['daily_pnl_fen'],-480)
        self.assertEqual(result['positions'][0]['daily_pnl_fen'],-480)
        account.update(cash_fen=20_002_120,receivable_fen=0)
        valuation['equity_fen']=20_002_120+97400
        payment=dict(code='600000',cash_delta_fen=2120,receivable_delta_fen=-2120)
        self.assertEqual(daily_performance(account,[],{'600000':974},valuation,[payment])['daily_pnl_fen'],0)
