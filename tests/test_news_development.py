import copy
import importlib
import importlib.util
import unittest

from experiments.news_processing.evidence import article_packet, company_packet
from tests.test_news_processing import record


class DevelopmentContractTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('experiments.news_processing.development'), 'Development contract is missing')
        self.d=importlib.import_module('experiments.news_processing.development')
        self.r=record()
        self.article=article_packet('steel',self.r['state']['news'])
        self.company=company_packet(self.r)
        self.nid=next(q['id'] for q in self.article['evidence'] if '普通中板' in q['text'])
        self.bid=next(q['id'] for q in self.company['evidence'] if q['text']=='钢材')
        self.fact={'fact_id':'F1','scope':'INDUSTRY_SIGNAL','kind':'PRICE','stage':'REPORTED',
            'subject_name':'','subject_code':'','description':'中板涨价','news_evidence_ids':[self.nid],
            'subject_evidence_ids':[],'temporal_role':'CURRENT','event_time_text':'','event_date':'',
            'time_precision':'UNKNOWN','time_evidence_ids':[]}
        self.label={'id':self.r['id'],'own_event':'NO','own_fact_ids':[],
            'links':[{'fact_id':'F1','transmission':'DIRECT','price_role':'INPUT_COST',
            'business_evidence_ids':[self.bid],'mechanism':'中板影响投入成本','missing_conditions':[]}]}

    def facts(self, facts=None, complete=True):
        return self.d.validate_facts(self.article,{'id':'steel','facts':facts if facts is not None else [self.fact],'extraction_complete':complete})

    def test_one_bad_fact_is_preserved_without_losing_valid_facts(self):
        bad={**self.fact,'fact_id':'F2','scope':'COMPANY_EVENT','subject_name':'不存在的公司','subject_evidence_ids':[]}
        checked=self.facts([self.fact,bad])
        self.assertEqual([f['fact_id'] for f in checked['facts']],['F1'])
        self.assertEqual(checked['quarantine'][0]['raw_fact'],bad)
        self.assertFalse(checked['complete'])
        self.assertEqual(checked['article_signal_presence'],'YES')

    def test_partial_extraction_cannot_become_company_no_event(self):
        checked=self.facts(complete=False)
        with self.assertRaises(ValueError):self.d.validate_pair(self.article,checked,self.company,self.label)
        answer=copy.deepcopy(self.label);answer['own_event']='UNKNOWN'
        result=self.d.validate_pair(self.article,checked,self.company,answer)
        self.assertEqual(result['coverage'],'partial');self.assertFalse(result['training_admitted'])

    def test_historical_fact_is_retained_but_not_active_signal(self):
        self.article=article_packet('steel',{'title':'背景','text':'2010年原研药获批。','market_rows':[]})
        ids=[q['id'] for q in self.article['evidence']]
        f={**self.fact,'news_evidence_ids':ids,'temporal_role':'HISTORICAL','event_time_text':'2010年',
           'time_precision':'YEAR','time_evidence_ids':ids,'kind':'REGULATION'}
        checked=self.facts([f])
        self.assertEqual(len(checked['facts']),1);self.assertEqual(checked['article_signal_presence'],'NO')
        answer=copy.deepcopy(self.label);answer['links'][0].update(price_role='NOT_APPLICABLE')
        with self.assertRaises(ValueError):self.d.validate_pair(self.article,checked,self.company,answer)

    def test_unknown_date_does_not_inherit_publication_day(self):
        self.article['published_date']='2026-10-13'
        from experiments.news_processing.contract import digest
        self.article['sha256']=digest({k:v for k,v in self.article.items() if k!='sha256'})
        checked=self.facts()
        self.assertEqual(checked['facts'][0]['event_date'],'')
        bad={**self.fact,'event_date':'2026-10-13'}
        self.assertEqual(len(self.facts([bad])['quarantine']),1)

    def test_time_evidence_must_be_literal_and_from_same_article(self):
        bad={**self.fact,'event_time_text':'2010年','time_precision':'YEAR','time_evidence_ids':[self.nid]}
        self.assertEqual(len(self.facts([bad])['quarantine']),1)

    def test_valid_explicit_day_preserves_precision(self):
        self.article=article_packet('steel',{'title':'签约','text':'2026年9月29日签署协议。','market_rows':[]})
        ids=[q['id'] for q in self.article['evidence']]
        f={**self.fact,'news_evidence_ids':ids,'event_time_text':'2026年9月29日','event_date':'2026-09-29',
           'time_precision':'DAY','time_evidence_ids':ids}
        self.assertEqual(self.facts([f])['facts'][0]['event_date'],'2026-09-29')

    def test_invented_day_for_year_precision_is_quarantined(self):
        self.article=article_packet('steel',{'title':'背景','text':'2010年获批。','market_rows':[]})
        ids=[q['id'] for q in self.article['evidence']]
        f={**self.fact,'news_evidence_ids':ids,'event_time_text':'2010年','event_date':'2010-01-01',
           'time_precision':'YEAR','time_evidence_ids':ids}
        self.assertEqual(len(self.facts([f])['quarantine']),1)

    def test_duplicate_fact_ids_are_quarantined_not_silently_selected(self):
        checked=self.facts([self.fact,{**self.fact,'description':'另一个事实'}])
        self.assertEqual(checked['facts'],[]);self.assertEqual(len(checked['quarantine']),2)
        self.assertEqual(checked['article_signal_presence'],'UNKNOWN')

    def test_unknown_time_is_not_promoted_to_active_industry_signal(self):
        checked=self.facts([{**self.fact,'temporal_role':'UNKNOWN'}])
        self.assertEqual(checked['article_signal_presence'],'UNKNOWN');self.assertFalse(checked['complete'])

    def test_unknown_scope_cannot_prove_absence_of_industry_signal(self):
        checked=self.facts([{**self.fact,'scope':'UNKNOWN'}])
        self.assertEqual(checked['article_signal_presence'],'UNKNOWN');self.assertFalse(checked['complete'])

    def test_future_day_cannot_be_reported_as_current_realized_event(self):
        from experiments.news_processing.contract import digest
        self.article=article_packet('steel',{'title':'计划','text':'预计2027年1月1日交割。','market_rows':[]})
        self.article['publication_date']='2026-10-13';self.article['sha256']=digest({k:v for k,v in self.article.items() if k!='sha256'})
        ids=[q['id'] for q in self.article['evidence']]
        f={**self.fact,'news_evidence_ids':ids,'event_time_text':'2027年1月1日','event_date':'2027-01-01','time_precision':'DAY','time_evidence_ids':ids}
        self.assertEqual(len(self.facts([f])['quarantine']),1)
        self.assertEqual(len(self.facts([{**f,'temporal_role':'PLANNED'}])['facts']),1)


if __name__=='__main__':unittest.main()
