import copy
import importlib
import importlib.util
import unittest

from tests.test_news_processing import record


class NewsEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('experiments.news_processing.evidence'), 'Evidence contract is not implemented')
        self.e = importlib.import_module('experiments.news_processing.evidence')
        self.r=record()
        self.article=self.e.article_packet('steel',self.r['state']['news'])
        self.company=self.e.company_packet(self.r)
        self.news_id=next(x['id'] for x in self.article['evidence'] if '普通中板' in x['text'])
        self.business_id=next(x['id'] for x in self.company['evidence'] if x['text']=='钢材')
        self.fact={'fact_id':'F1','scope':'INDUSTRY_SIGNAL','kind':'PRICE','stage':'REPORTED',
                   'subject_name':'','subject_code':'','news_evidence_ids':[self.news_id],
                   'subject_evidence_ids':[],'description':'普通中板价格上涨'}
        self.label={'id':self.r['id'],'own_event':'NO','own_fact_ids':[],
                    'links':[{'fact_id':'F1','transmission':'DIRECT','price_role':'INPUT_COST',
                              'business_evidence_ids':[self.business_id],
                              'mechanism':'钢材价格影响工程机械投入成本','missing_conditions':[]}]}

    def checked(self):
        return self.e.validate_facts(self.article,{'id':'steel','facts':[self.fact]})

    def test_quote_resolution_preserves_unicode_and_exact_offsets(self):
        a=self.e.article_packet('unicode',{'title':'价格😀','text':'钢材上涨。\r\n铜材持平。','market_rows':[]})
        for q in a['evidence']:
            self.assertEqual(a['visible'][q['start']:q['end']],q['text'])
        self.assertEqual(''.join(x['text'] for x in a['evidence']),a['visible'])

    def test_table_headers_units_and_data_are_all_visible(self):
        news={'title':'价格','text':'单位：元/吨','market_rows':[['商品','价格'],['钢材','3000']]}
        a=self.e.article_packet('table',news)
        self.assertIn('商品 | 价格',a['visible']); self.assertIn('钢材 | 3000',a['visible'])
        row=next(q for q in a['evidence'] if '钢材 | 3000' in q['text'])
        self.assertIn('钢材 | 3000',self.e.resolve(a,[row['id']])[0]['quote'])

    def test_foreign_article_and_unknown_evidence_ids_are_rejected(self):
        foreign=self.e.article_packet('other',self.r['state']['news'])['evidence'][0]['id']
        for eid in [foreign,'invented']:
            bad=copy.deepcopy(self.fact);bad['news_evidence_ids']=[eid]
            with self.assertRaises(ValueError):self.e.validate_facts(self.article,{'id':'steel','facts':[bad]})

    def test_article_pack_has_no_target_profile_or_prior_labels(self):
        self.r['expected']={'own_event':'YES'}
        a=self.e.article_packet('steel',self.r['state']['news'])
        self.assertNotIn('company',a);self.assertNotIn('expected',a)
        b=self.e.company_packet(self.r)
        self.assertNotIn('expected',b);self.assertNotIn('reviewer_note',b)

    def test_tampered_evidence_offsets_fail_before_label_use(self):
        a=copy.deepcopy(self.article);a['evidence'][0]['start']+=1
        with self.assertRaises(ValueError):self.e.validate_facts(a,{'id':'steel','facts':[self.fact]})

    def test_same_article_signal_is_preserved_for_unrelated_target(self):
        facts=self.checked();label=copy.deepcopy(self.label)
        label['links'][0].update(transmission='NONE',price_role='NOT_APPLICABLE',business_evidence_ids=[],mechanism='未发现业务连接')
        checked=self.e.validate_pair(self.article,facts,self.company,label)
        self.assertEqual(checked['article_signal_presence'],'YES')
        self.assertEqual(checked['links'][0]['transmission'],'NONE')

    def test_price_role_is_separate_from_article_price_fact(self):
        checked=self.e.validate_pair(self.article,self.checked(),self.company,self.label)
        self.assertEqual(checked['links'][0]['price_role'],'INPUT_COST')
        self.assertEqual(checked['resolved_business_evidence'][0]['quote'],'钢材')
        self.assertFalse(checked['human_verified'])

    def test_nonprice_fact_cannot_claim_input_cost_or_output_price(self):
        fact=copy.deepcopy(self.fact);fact['kind']='DEMAND'
        facts=self.e.validate_facts(self.article,{'id':'steel','facts':[fact]})
        with self.assertRaises(ValueError):self.e.validate_pair(self.article,facts,self.company,self.label)

    def test_all_extracted_facts_must_receive_a_target_link(self):
        label=copy.deepcopy(self.label);label['links']=[]
        with self.assertRaises(ValueError):self.e.validate_pair(self.article,self.checked(),self.company,label)

    def test_missing_arrays_are_not_silently_filled(self):
        label=copy.deepcopy(self.label);del label['links'][0]['missing_conditions']
        with self.assertRaises(ValueError):self.e.validate_pair(self.article,self.checked(),self.company,label)

    def test_conditional_link_requires_missing_conditions(self):
        label=copy.deepcopy(self.label);label['links'][0]['transmission']='CONDITIONAL'
        with self.assertRaises(ValueError):self.e.validate_pair(self.article,self.checked(),self.company,label)
        label['links'][0]['missing_conditions']=['需确认实际钢材品种']
        self.assertEqual(self.e.validate_pair(self.article,self.checked(),self.company,label)['links'][0]['transmission'],'CONDITIONAL')

    def test_foreign_company_evidence_is_rejected(self):
        r=record();r['target_code']='600276';r['state']['company']['name']='恒瑞医药'
        label=copy.deepcopy(self.label);label['links'][0]['business_evidence_ids']=[self.e.company_packet(r)['evidence'][0]['id']]
        with self.assertRaises(ValueError):self.e.validate_pair(self.article,self.checked(),self.company,label)

    def test_unknown_empty_fact_extraction_does_not_become_no_signal(self):
        facts=self.e.validate_facts(self.article,{'id':'steel','facts':[]})
        label=copy.deepcopy(self.label);label.update(own_event='UNKNOWN',links=[])
        checked=self.e.validate_pair(self.article,facts,self.company,label)
        self.assertEqual(checked['article_signal_presence'],'UNKNOWN')
        self.assertEqual(checked['own_event'],'UNKNOWN')

    def test_own_event_requires_matching_news_side_subject(self):
        label=copy.deepcopy(self.label);label.update(own_event='YES',own_fact_ids=['F1'])
        with self.assertRaises(ValueError):self.e.validate_pair(self.article,self.checked(),self.company,label)

    def test_truncated_security_code_is_not_identity_evidence(self):
        fact=copy.deepcopy(self.fact);fact.update(scope='COMPANY_EVENT',subject_name='',subject_code='600',subject_evidence_ids=[self.news_id])
        a=self.e.article_packet('steel',{'title':'证券代码600276','text':'获批','market_rows':[]})
        ids=[x['id'] for x in a['evidence']];fact.update(news_evidence_ids=ids,subject_evidence_ids=ids)
        with self.assertRaises(ValueError):self.e.validate_facts(a,{'id':'steel','facts':[fact]})

    def test_matching_name_cannot_override_conflicting_security_code(self):
        a=self.e.article_packet('steel',{'title':'三一重工','text':'证券代码600276 获批','market_rows':[]})
        ids=[x['id'] for x in a['evidence']]
        fact={**self.fact,'scope':'COMPANY_EVENT','kind':'REGULATION','subject_name':'三一重工','subject_code':'600276','news_evidence_ids':ids,'subject_evidence_ids':ids}
        facts=self.e.validate_facts(a,{'id':'steel','facts':[fact]})
        label=copy.deepcopy(self.label);label.update(own_event='YES',own_fact_ids=['F1'])
        label['links'][0].update(transmission='NONE',price_role='NOT_APPLICABLE')
        with self.assertRaises(ValueError):self.e.validate_pair(a,facts,self.company,label)

    def test_foreign_counterparty_ticker_can_be_preserved_as_literal_fact(self):
        a=self.e.article_packet('foreign',{'title':'合作方','text':'Novo Nordisk证券代码NOVO-B签约。','market_rows':[]})
        ids=[x['id'] for x in a['evidence']]
        fact={**self.fact,'scope':'COMPANY_EVENT','kind':'CONTRACT','subject_name':'Novo Nordisk','subject_code':'NOVO-B','news_evidence_ids':ids,'subject_evidence_ids':ids}
        self.assertEqual(self.e.validate_facts(a,{'id':'foreign','facts':[fact]})['facts'][0]['subject_code'],'NOVO-B')

    def test_ticker_prefix_cannot_match_market_suffix_or_another_ticker(self):
        for code,text in [('600276','600276.SH'),('ABC','ABC-1')]:
            a=self.e.article_packet('prefix',{'title':'代码','text':text,'market_rows':[]});ids=[q['id'] for q in a['evidence']]
            fact={**self.fact,'scope':'COMPANY_EVENT','subject_name':'','subject_code':code,'news_evidence_ids':ids,'subject_evidence_ids':ids}
            with self.assertRaises(ValueError):self.e.validate_facts(a,{'id':'prefix','facts':[fact]})

    def test_ascii_subject_name_cannot_match_longer_entity_token(self):
        a=self.e.article_packet('name',{'title':'合作','text':'ABCD签约','market_rows':[]});ids=[q['id'] for q in a['evidence']]
        fact={**self.fact,'scope':'COMPANY_EVENT','subject_name':'ABC','subject_code':'','news_evidence_ids':ids,'subject_evidence_ids':ids}
        with self.assertRaises(ValueError):self.e.validate_facts(a,{'id':'name','facts':[fact]})

    def test_company_event_is_not_universal_industry_rule_but_peer_is_retained(self):
        news={'title':'甲公司药品获批','text':'证券代码：600276。恒瑞医药新药上市获批。','market_rows':[]}
        a=self.e.article_packet('approval',news)
        ids=[q['id'] for q in a['evidence']]
        fact={**self.fact,'scope':'COMPANY_EVENT','kind':'REGULATION','stage':'APPROVED',
              'subject_name':'恒瑞医药','subject_code':'600276','subject_evidence_ids':ids,'news_evidence_ids':ids}
        facts=self.e.validate_facts(a,{'id':'approval','facts':[fact]})
        peer=copy.deepcopy(self.label);peer['links'][0].update(transmission='CONDITIONAL',price_role='NOT_APPLICABLE',missing_conditions=['待确认产品竞争关系'])
        checked=self.e.validate_pair(a,facts,self.company,peer)
        self.assertEqual(checked['article_signal_presence'],'NO')
        self.assertEqual(checked['links'][0]['transmission'],'CONDITIONAL')
        r=record();r['target_code']='600276';r['state']['company']['name']='恒瑞医药'
        own=self.e.company_packet(r);label=copy.deepcopy(peer);label.update(id=r['id'],own_event='YES',own_fact_ids=['F1'])
        label['links'][0]['business_evidence_ids']=[]
        with self.assertRaises(ValueError):self.e.validate_pair(a,facts,own,label)
        label['links'][0].update(transmission='NONE',missing_conditions=[])
        self.assertEqual(self.e.validate_pair(a,facts,own,label)['own_event'],'YES')


if __name__=='__main__':unittest.main()
