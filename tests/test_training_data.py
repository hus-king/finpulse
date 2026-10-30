import copy
import json
import tempfile
import unittest
from pathlib import Path
from experiments.news_processing import training_data as t
from experiments.news_processing import development as d
from experiments.news_processing.contract import digest
from experiments.news_processing.data_evidence import time_candidates
from experiments.news_processing.storage import write_json,write_jsonl,read_jsonl
from tests.test_news_processing import record

class TrainingDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        r=record();r['article_id']='steel';r['source']={};r['state']['news']={'title':'2026年上半年钢材','text':'钢材价格上涨。','market_rows':[]}
        a=d.article_packet(r);self.article={'packet':a,'time_candidates':time_candidates(a)}
        self.unit={'id':'steel:F1','task':'fact_structuring','article_id':'steel','lineage':{'group_id':'g','split':'calibration'},
            'proposal':{'fact_id':'F1','scope':'INDUSTRY_SIGNAL','kind':'PRICE','stage':'REPORTED','subject_name':'','subject_code':'',
            'description':'钢材价格上涨','news_evidence_ids':[a['evidence'][1]['id']],'subject_evidence_ids':[],
            'temporal_role':'ONGOING','time_candidate_id':self.article['time_candidates'][0]['id']}}
        self.packet={'task':'fact_structuring','articles':[self.article],'units':[self.unit]};self.packet['sha256']=digest(self.packet)
        (self.root/'review_packets').mkdir();write_json(self.root/'review_packets'/'fact.json',self.packet)
        self.answer={'id':self.unit['id'],'verdict':'APPROVE','patch':{},'reason':'原文支持该价格事实和报告区间'}

    def test_review_cannot_invent_date_or_borrow_foreign_evidence(self):
        for patch in [{'event_date':'2026-01-01'},{'news_evidence_ids':['foreign']},{'time_candidate_id':'foreign'}]:
            a={**self.answer,'verdict':'REVISE','patch':patch}
            row=t.validate_reviews(self.packet,{'reviews':[a]},'Codex')[0]
            self.assertFalse(row['evidence_valid']);self.assertEqual(row['raw_review'],a)

    def test_valid_review_retains_canonical_source_time_and_nonhuman_status(self):
        row=t.validate_reviews(self.packet,{'reviews':[self.answer]},'Codex')[0]
        self.assertTrue(row['evidence_valid']);self.assertEqual(row['canonical']['event_date'],'')
        self.assertEqual(row['canonical']['event_time_text'],'2026年上半年');self.assertFalse(row['human_verified'])

    def test_duplicate_reviews_and_malformed_entries_are_retained(self):
        rows=t.validate_reviews(self.packet,{'reviews':[self.answer,self.answer,None]},'Codex')
        self.assertFalse(rows[0]['evidence_valid']);self.assertEqual(len(rows[0]['raw_reviews']),2)
        self.assertEqual(len(rows),2)

    def test_single_reviewer_or_disagreement_never_enters_silver(self):
        write_json(self.root/'articles.json',[self.article]);write_jsonl(self.root/'fact_candidates.jsonl',[self.unit]);write_jsonl(self.root/'relation_candidates.jsonl',[])
        rows=t.validate_reviews(self.packet,{'reviews':[self.answer]},'Codex');write_jsonl(self.root/'semantic_reviews.jsonl',rows)
        self.assertEqual(t.export(self.root)['silver_facts'],0)
        altered={**self.answer,'verdict':'REVISE','patch':{'temporal_role':'CURRENT'}}
        rows+=t.validate_reviews(self.packet,{'reviews':[altered]},'Copilot');write_jsonl(self.root/'semantic_reviews.jsonl',rows)
        self.assertEqual(t.export(self.root)['silver_facts'],0)
        self.assertIn('reviewer_disagreement',str(read_jsonl(self.root/'exclusions.jsonl')))

    def test_two_source_reviews_export_actual_messages_and_empty_human_release(self):
        write_json(self.root/'articles.json',[self.article]);write_jsonl(self.root/'fact_candidates.jsonl',[self.unit]);write_jsonl(self.root/'relation_candidates.jsonl',[])
        rows=sum([t.validate_reviews(self.packet,{'reviews':[self.answer]},p) for p in ['Codex','Copilot']],[])
        write_jsonl(self.root/'semantic_reviews.jsonl',rows);summary=t.export(self.root)
        self.assertEqual(summary['silver_facts'],1);self.assertEqual(summary['human_verified'],0)
        example=read_jsonl(self.root/'silver_candidates.jsonl')[0]
        self.assertEqual(example['task'],'fact_structuring');self.assertFalse(example['training_admitted'])
        self.assertEqual(example['split'],'development');self.assertEqual(example['lineage']['parent_split'],'calibration')
        self.assertNotIn('reviews',example);self.assertNotIn('target',example);self.assertNotIn('input',example)
        self.assertEqual(json.loads(example['messages'][-1]['content'])['temporal_role'],'ONGOING')
        self.assertEqual(read_jsonl(self.root/'human_confirmed.jsonl'),[])

    def test_packet_or_candidate_change_is_rejected(self):
        changed=copy.deepcopy(self.packet);changed['units'][0]['proposal']['description']='篡改'
        with self.assertRaises(ValueError):t.validate_reviews(changed,{'reviews':[self.answer]},'Codex')

    def test_missing_exact_date_does_not_exclude_a_source_supported_current_event(self):
        u=copy.deepcopy(self.unit);u['proposal']['time_candidate_id']=None;u['proposal']['temporal_role']='CURRENT'
        p={**self.packet,'units':[u]};p['sha256']=digest({k:v for k,v in p.items() if k!='sha256'})
        rows=sum([t.validate_reviews(p,{'reviews':[self.answer]},name) for name in ['Codex','Copilot']],[])
        self.assertIsNotNone(t._agreement(u,rows)[0])

    def test_export_revalidates_saved_review_instead_of_trusting_valid_flag(self):
        write_json(self.root/'articles.json',[self.article]);write_jsonl(self.root/'fact_candidates.jsonl',[self.unit]);write_jsonl(self.root/'relation_candidates.jsonl',[])
        rows=sum([t.validate_reviews(self.packet,{'reviews':[self.answer]},p) for p in ['Codex','Copilot']],[])
        rows[0]['canonical']['kind']='DEMAND';write_jsonl(self.root/'semantic_reviews.jsonl',rows)
        with self.assertRaises(ValueError):t.export(self.root)

    def test_unrelated_time_is_not_automatically_attached_to_fact(self):
        r=record();r['article_id']='years';r['source']={}
        r['state']['news']={'title':'年度事项','text':'2024年有历史事项。\n2025年生产增长。','market_rows':[]}
        a=d.article_packet(r);article={'packet':a,'time_candidates':time_candidates(a)}
        ids=[q['id'] for q in a['evidence'] if '2025年' in q['text']]
        self.assertIsNone(t._choose_time(article,{'event_time_text':'2024年','news_evidence_ids':ids}))

    def test_partial_table_is_preserved_but_not_released_as_candidates(self):
        parent=self.root/'parent';(parent/'raw').mkdir(parents=True)
        (parent/'raw'/'one.bin').write_bytes(b'fixture')
        r=record();r.update(article_id='table',source={},group_id='g',split='development')
        r['state']['news']={'title':'2026年5月上旬价格','text':'','market_rows':[
            ['产品名称','单位','本期价格（元）','比上期涨跌','涨跌幅'],['钢材','吨','100','5','5.3'],['纸浆','吨','--','5','1']]}
        write_jsonl(parent/'inputs.jsonl',[r]);write_json(parent/'artifact_checksums.json',{'files':{
            str(p.relative_to(parent)):t.sha(p) for p in parent.rglob('*') if p.is_file()}})
        result=t.prepare(parent,self.root/'next')
        self.assertEqual(result['fact_candidates'],0)
        self.assertEqual(json.loads((self.root/'next'/'table_plans.json').read_text())[0]['pending_rows'],[2])

if __name__=='__main__':unittest.main()

class EventRelationTests(unittest.TestCase):
    def setUp(self):
        fixture=TrainingDataTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.p=copy.deepcopy(fixture.packet);self.p['task']='event_target_relation'
        unit=self.p['units'][0];unit.update(task='event_target_relation',company=d.company_packet(record()),
            fact=t._canonical_fact(self.p['articles'][0],unit['proposal']),proposal={})
        self.p['sha256']=digest({k:v for k,v in self.p.items() if k!='sha256'})
        bid=next(q['id'] for q in unit['company']['evidence'] if q['text']=='钢材')
        self.a={'id':unit['id'],'verdict':'REVISE','reason':'原材料品种暴露仍需证据','patch':{
            'event_owner':'INDUSTRY','relation':'CONDITIONAL','price_role':'INPUT_COST',
            'news_evidence_ids':unit['fact']['news_evidence_ids'],'business_evidence_ids':[bid],
            'missing_conditions':['具体采购品种待证'],'mechanism':'行业钢材价格与已披露钢材投入有关，尚需具体品种支持'}}

    def test_industry_fact_cannot_become_target_own_event(self):
        self.a['patch'].update(event_owner='TARGET',relation='OWN')
        self.assertFalse(t.validate_reviews(self.p,{'reviews':[self.a]},'Copilot')[0]['evidence_valid'])

    def test_event_relation_requires_concrete_business_and_conditions(self):
        self.assertTrue(t.validate_reviews(self.p,{'reviews':[self.a]},'Copilot')[0]['evidence_valid'])
        for patch in [{'business_evidence_ids':[]},{'missing_conditions':[]},{'own_event':'NO'}]:
            a=copy.deepcopy(self.a);a['patch'].update(patch)
            self.assertFalse(t.validate_reviews(self.p,{'reviews':[a]},'Copilot')[0]['evidence_valid'])
