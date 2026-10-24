import copy
import tempfile
import unittest
from pathlib import Path
from experiments.news_processing import silver_trial as s
from experiments.news_processing.contract import digest,canonical
from experiments.news_processing.storage import write_jsonl,read_jsonl


def example(id_,group,label,task='fact_structuring'):
    src={'article':{'publication_date':'2026-05-14','evidence':[{'id':'title','text':'2026年5月市场统计\n'},
         {'id':'fact','text':'钢材价格上涨。'}]},'fact_evidence_ids':['fact'],'time_candidates':[]}
    target={'scope':label}
    if task=='event_target_relation':
        src.update(event={'news_evidence_ids':['fact'],'description':'不应作为唯一新闻证据'},
                   company={'company':{'name':'某公司','code':'600001','products':['钢材'],'upstream':[],'downstream':[],'regions':[]}})
        target={'relation':label,'event_owner':'INDUSTRY','price_role':'INPUT_COST'}
    r={'id':id_,'task':task,'lineage':{'group_id':group,'article_id':id_.split(':')[0]},
       'human_verified':False,'training_admitted':False,'label_tier':'source_supported_silver_candidate',
       'messages':[{'role':'system','content':'原文判断'},{'role':'user','content':canonical(src)},{'role':'assistant','content':canonical(target)}]}
    r['sha256']=digest(r);return r

class SilverTrialTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.rows=[example('a:F1','a','COMPANY_EVENT'),example('b:F1','b','INDUSTRY_SIGNAL')]
        self.rows += [example('r'+str(i)+':F1','r'+str(i),v,'event_target_relation') for i,v in enumerate(['OWN','DIRECT','CONDITIONAL','NONE'])]
        self.rows += [example('held:F1','held','COMPANY_EVENT')]
        write_jsonl(self.root/'silver_candidates.jsonl',self.rows)

    def prepare(self,**kwargs):
        return s.prepare(self.root,self.root/'run',heldout_groups=['held'],research_silver=True,
                         base_checkpoint='/cached/base',base_weight_sha256='a'*64,**kwargs)

    def test_source_gate_requires_explicit_research_opt_in(self):
        with self.assertRaises(ValueError):s.prepare(self.root,self.root/'run',heldout_groups=['held'],research_silver=False,base_checkpoint='/base',base_weight_sha256='a'*64)

    def test_projection_excludes_answer_and_review_fields(self):
        r=s.adapt(self.rows[0]);self.assertEqual(r['expected'],{'fact_scope':'COMPANY_EVENT'})
        self.assertNotIn('COMPANY_EVENT',canonical(r['state']));self.assertIn('钢材价格上涨',canonical(r['state']))
        self.assertNotIn('reviewer',canonical(r['state']))

    def test_group_split_is_frozen_before_baseline_and_source_stays_unchanged(self):
        summary=self.prepare();self.assertEqual(summary['train'],6);self.assertEqual(summary['development'],1)
        self.assertTrue(all(r['group_id']!='held' for r in read_jsonl(self.root/'run'/'train.jsonl')))
        self.assertFalse(read_jsonl(self.root/'silver_candidates.jsonl')[0]['training_admitted'])
        with self.assertRaises(ValueError):self.prepare()

    def test_missing_training_class_is_not_filled_with_none(self):
        self.rows=[r for r in self.rows if r['id']!='r1:F1'];write_jsonl(self.root/'silver_candidates.jsonl',self.rows)
        with self.assertRaises(ValueError):self.prepare()

    def test_unsupported_relation_and_tampered_source_are_rejected(self):
        for mode in ['label','digest']:
            r=copy.deepcopy(self.rows[2])
            if mode=='label':r['messages'][-1]['content']=canonical({'relation':'DISTANT'});r['sha256']=digest({k:v for k,v in r.items() if k!='sha256'})
            else:r['human_verified']=True
            with self.assertRaises(ValueError):s.adapt(r)

    def test_unsupported_development_class_has_null_metric(self):
        rows=[{'question':'event_relation','expected':'NONE','label':'NONE'}]
        m=s.metrics(rows)['event_relation']
        self.assertIsNone(m['class_recall']['DIRECT']['recall']);self.assertEqual(m['supported_macro_f1'],1.0)

    def test_one_article_cannot_cross_role_through_different_group_labels(self):
        r=example('held:F2','other','INDUSTRY_SIGNAL');self.rows.append(r);write_jsonl(self.root/'silver_candidates.jsonl',self.rows)
        with self.assertRaises(ValueError):self.prepare()
