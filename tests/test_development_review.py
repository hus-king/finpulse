import copy
import tempfile
import unittest
from pathlib import Path
from experiments.news_processing import development_review as r
from experiments.news_processing.storage import write_jsonl,read_jsonl
from tests.test_news_processing import record

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        x=record();x['article_id']='steel';x['source']={};write_jsonl(self.root/'inputs.jsonl',[x])
        self.packet=r.prepare_blind(self.root,batch_articles=4)[0]
        p=self.packet['pairs'][0];a=self.packet['articles'][0];c=self.packet['companies'][0]
        self.answer={'id':p['id'],'own_event':'NO','own_fact':None,'mechanisms':[{
            'kind':'PRICE','temporal_role':'CURRENT','transmission':'CONDITIONAL','price_role':'INPUT_COST',
            'news_evidence_ids':[next(e['id'] for e in a['evidence'] if '普通中板' in e['text'])],
            'business_evidence_ids':[next(e['id'] for e in c['evidence'] if e['text']=='钢材')],
            'missing_conditions':['具体钢材品种采购待证'],'reason':'需要核对采购暴露'}],'uncertainty_notes':[]}

    def test_packet_has_no_initial_answers_and_failed_row_is_retained(self):
        self.assertNotIn('initial_label',self.packet);self.assertNotIn('expected_label',str(self.packet))
        self.assertFalse(self.packet['teacher_answers_visible'])
        bad=copy.deepcopy(self.answer);bad['mechanisms'][0]['news_evidence_ids']=['foreign']
        rows=r.validate_blind(self.packet,{'reviews':[bad]},'Gemini')
        self.assertFalse(rows[0]['evidence_valid']);self.assertEqual(rows[0]['raw_review'],bad)
        self.assertFalse(rows[0]['human_verified']);self.assertFalse(rows[0]['training_admitted'])

    def test_valid_ai_review_stays_weak_reference(self):
        rows=r.validate_blind(self.packet,{'reviews':[self.answer]},'Gemini')
        self.assertTrue(rows[0]['evidence_valid']);self.assertIn('resolved_news_evidence',rows[0]['review']['mechanisms'][0])
        self.assertEqual(rows[0]['reference_type'],'independent_AI_weak_review')

    def test_missing_duplicate_and_foreign_answers_never_disappear(self):
        rows=r.validate_blind(self.packet,{'reviews':[self.answer,self.answer,{'id':'foreign'}]},'Gemini')
        self.assertEqual(len(rows),2);self.assertEqual(rows[0]['error'],'Missing or duplicate review ID')
        self.assertEqual(len(rows[0]['raw_reviews']),2)
        self.assertEqual(rows[1]['error'],'Foreign review ID')

    def test_malformed_mechanism_is_quarantined_and_other_rows_survive(self):
        bad=copy.deepcopy(self.answer);bad['mechanisms']=[None]
        rows=r.validate_blind(self.packet,{'reviews':[bad,None,{'id':[]}]},'Gemini')
        self.assertEqual(len(rows),3);self.assertFalse(rows[0]['evidence_valid'])
        self.assertEqual(rows[0]['raw_review'],bad)
        write_jsonl(self.root/'independent_reviews.jsonl',rows)
        self.assertEqual(r.export(self.root)['pairs'],1)

    def test_historical_direct_claim_is_pending(self):
        bad=copy.deepcopy(self.answer);bad['mechanisms'][0].update(temporal_role='HISTORICAL',transmission='DIRECT')
        self.assertFalse(r.validate_blind(self.packet,{'reviews':[bad]},'Gemini')[0]['evidence_valid'])

    def test_own_identity_binding_does_not_invalidate_business_coordinates(self):
        from experiments.news_processing.evidence import article_packet
        from experiments.news_processing.contract import digest
        p=copy.deepcopy(self.packet)
        a=article_packet('steel',{'title':'回购','text':'证券代码600031，近日提议回购。','market_rows':[]})
        p['articles']=[a];p['sha256']=digest({k:v for k,v in p.items() if k!='sha256'})
        ids=[q['id'] for q in a['evidence']];answer=copy.deepcopy(self.answer)
        answer.update(own_event='YES',own_fact={'subject_name':'','subject_code':'600031','subject_evidence_ids':ids,
            'temporal_role':'PLANNED','event_time_text':'近日','event_date':'','time_precision':'RELATIVE','time_evidence_ids':ids})
        answer['mechanisms'][0].update(kind='CORPORATE_ACTION',transmission='NONE',price_role='NOT_APPLICABLE',news_evidence_ids=ids)
        self.assertTrue(r.validate_blind(p,{'reviews':[answer]},'Gemini')[0]['evidence_valid'])

    def test_dashboard_escapes_sources_and_keeps_every_pair(self):
        rows=r.validate_blind(self.packet,{'reviews':[self.answer]},'Gemini')
        write_jsonl(self.root/'independent_reviews.jsonl',rows)
        x=read_jsonl(self.root/'inputs.jsonl')[0];x['state']['news']['title']='</script><script>alert(1)</script>'
        write_jsonl(self.root/'inputs.jsonl',[x]);summary=r.export(self.root)
        html=(self.root/'development_review.html').read_text()
        self.assertEqual(summary['pairs'],1);self.assertEqual(summary['human_verified'],0)
        self.assertNotIn('</script><script>alert(1)</script>',html)
        self.assertIn('人工确认',html)

    def test_partial_unknown_and_blind_no_are_coverage_difference(self):
        write_jsonl(self.root/'independent_reviews.jsonl',r.validate_blind(self.packet,{'reviews':[self.answer]},'Gemini'))
        (self.root/'teacher').mkdir()
        write_jsonl(self.root/'teacher'/'pairs_primary.jsonl',[{'id':self.answer['id'],'evidence_valid':True,
            'answer':{'own_event':'UNKNOWN','coverage':'partial'}}])
        summary=r.export(self.root)
        self.assertEqual(summary['own_event_disagreement_pairs'],0)
        self.assertEqual(summary['coverage_state_difference_pairs'],1)
