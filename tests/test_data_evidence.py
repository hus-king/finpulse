import copy
import unittest
from experiments.news_processing import data_evidence as x
from experiments.news_processing import development as d
from tests.test_news_processing import record

class EvidenceUnitTests(unittest.TestCase):
    def setUp(self):
        self.r=record();self.r['article_id']='prices';self.r['source']={}
        self.r['state']['news']={'title':'2026年5月上旬价格','text':'上期为2026年4月下旬。','market_rows':[
            ['产品名称','单位','本期价格（元）','比上期涨跌（元）','涨跌幅（%）'],
            ['一、原料','','','',''],['钢材','吨','100.0','5.0','5.3'],['饲料','吨','90.0','-2.0','-2.2'],
            ['化肥','吨','80','0.0','0.0'],['注：上期为2026年4月下旬。'],
            ['序号','监测产品','规格型号','说明'],['1','钢材','Q235','用途说明']]}

    def test_all_rows_and_comparison_context_survive_bounded_blocks(self):
        plan=x.table_units(self.r,block_size=2);x.validate_table(plan,self.r)
        self.assertEqual(plan['total_rows'],8);self.assertEqual(plan['price_rows'],[2,3,4])
        self.assertEqual([b['row_indices'] for b in plan['blocks']],[[2,3],[4]])
        self.assertEqual([p['direction'] for b in plan['blocks'] for p in b['prices']],['UP','DOWN','FLAT'])
        self.assertEqual(plan['ledger'][7]['role'],'specification');self.assertTrue(plan['complete'])
        self.assertIn('2026年4月下旬',str(plan['context']))

    def test_missing_duplicate_or_reordered_block_is_rejected(self):
        original=x.table_units(self.r,block_size=2)
        for indices in [[2,3],[2,3,3],[3,2,4]]:
            plan=copy.deepcopy(original);plan['blocks']=[{**plan['blocks'][0],'row_indices':indices}]
            with self.assertRaises(ValueError):x.validate_table(plan,self.r)

    def test_malformed_numeric_row_remains_pending_not_silently_skipped(self):
        self.r['state']['news']['market_rows'][2][2]='--'
        plan=x.table_units(self.r)
        self.assertEqual(plan['pending_rows'],[2]);self.assertFalse(plan['complete'])

    def test_zero_change_with_nonzero_percent_is_pending(self):
        self.r['state']['news']['market_rows'][2][3:]=['0','5']
        plan=x.table_units(self.r)
        self.assertEqual(plan['pending_rows'],[2]);self.assertFalse(plan['complete'])

    def test_row_value_or_unit_tamper_is_rejected(self):
        plan=x.table_units(self.r);plan['blocks'][0]['prices'][0]['unit']='千克'
        with self.assertRaises(ValueError):x.validate_table(plan,self.r)

    def test_cross_line_exact_day_and_ranges_never_invent_dates(self):
        self.r['state']['news']={'title':'2026年上半年统计','text':'2010 年背景。2026 年9 月29 日签约；近日预计2026年第四季度交割，上半年累计。','market_rows':[]}
        a=d.article_packet(self.r);cs=x.time_candidates(a)
        bytext={c['normalized']:c for c in cs}
        self.assertEqual(bytext['2026年9月29日']['event_date'],'2026-09-29')
        self.assertEqual(bytext['2010年']['event_date'],'')
        self.assertEqual(bytext['2026年上半年']['precision'],'RANGE')
        self.assertEqual(bytext['近日']['precision'],'RELATIVE')
        self.assertEqual(bytext['2026年第四季度']['event_date'],'')
        for c in cs:self.assertEqual(a['visible'][c['start']:c['end']],c['quote'])

    def test_invalid_calendar_day_is_not_a_valid_candidate(self):
        self.r['state']['news']={'title':'日期','text':'2026年2月30日，金额3亿元。','market_rows':[]}
        cs=x.time_candidates(d.article_packet(self.r))
        self.assertFalse(any(c['precision']=='DAY' for c in cs))

    def test_asof_month_end_keeps_relative_qualifier(self):
        self.r['state']['news']={'title':'订单','text':'截至6月底，手持订单增长。','market_rows':[]}
        cs=x.time_candidates(d.article_packet(self.r))
        self.assertEqual([(c['normalized'],c['precision']) for c in cs],[('截至6月底','RELATIVE')])

if __name__=='__main__':unittest.main()
