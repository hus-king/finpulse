import copy
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from experiments.news_processing import evidence_pipeline as p
from experiments.news_processing.storage import read_jsonl,write_jsonl
from tests.test_news_processing import record


class DevelopmentRunnerTests(unittest.TestCase):
    def setUp(self):
        self.assertIn('contract',inspect.signature(p.run).parameters,'Runner does not support additive contracts')
        from experiments.news_processing import development as d
        self.d=d;self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'first';self.root.mkdir()
        self.r=record();self.r['article_id']='steel';write_jsonl(self.root/'inputs.jsonl',[self.r])
        self.config={'model':'fake','base_url':'https://example.test','api_key':'secret'}
        self.policy={'max_attempts':8,'token_soft_stop':100000,'token_hard_cap':100000,
                     'workers':1,'batch_size':2,'attempts_per_batch':2,'max_output_tokens':4000}
        self.calls=[]

    def request(self,url,payload,key):
        wire=json.loads(payload['messages'][1]['content']);self.calls.append(wire)
        if isinstance(wire,list):
            a=wire[0]['article'];eid=a['evidence'][0]['id']
            fact={'fact_id':'F1','scope':'INDUSTRY_SIGNAL','kind':'PRICE','stage':'REPORTED','subject_name':'','subject_code':'',
                  'description':'钢价','news_evidence_ids':[eid],'subject_evidence_ids':[],
                  'temporal_role':'CURRENT','event_time_text':'','event_date':'','time_precision':'UNKNOWN','time_evidence_ids':[]}
            bad={**fact,'fact_id':'F2','scope':'COMPANY_EVENT','subject_name':'未证明的主体'}
            answer={'id':a['id'],'facts':[fact,bad],'extraction_complete':True}
        else:
            c=wire['targets'][0]
            answer={'id':c['pair_id'],'own_event':'UNKNOWN','own_fact_ids':[],'links':[{'fact_id':'F1','transmission':'NONE',
                    'price_role':'NOT_APPLICABLE','business_evidence_ids':[],'mechanism':'对已知事实无连接，整篇仍待补证','missing_conditions':[]}]}
        return {'usage':{'total_tokens':10},'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'answers':[answer]})}}]}

    def test_valid_facts_continue_after_quarantine_without_claiming_full_coverage(self):
        summary=p.run(self.root,self.config,self.policy,self.request,contract=self.d,modes=('primary',))
        self.assertEqual(len(self.calls),2)
        self.assertEqual(summary['primary_structurally_valid'],1)
        queue=read_jsonl(self.root/'review_queue.jsonl')[0]
        self.assertEqual(queue['status'],'pending_article_partial')
        self.assertNotIn('F2',[f['fact_id'] for f in self.calls[1]['facts']['facts']])
        p.run(self.root,self.config,self.policy,self.request,contract=self.d,modes=('primary',))
        self.assertEqual(len(self.calls),2)

    def test_new_experiment_does_not_reset_global_unknown_request_budget(self):
        self.policy['token_hard_cap']=20000
        ledger=self.base/'shared_budget'
        def fail(*args):self.calls.append(args);raise RuntimeError('unknown usage')
        p.run(self.root,self.config,self.policy,fail,contract=self.d,modes=('primary',),shared_budget_root=ledger)
        second=self.base/'second';second.mkdir();r=copy.deepcopy(self.r);r['article_id']='other';r['id']='other__600031'
        write_jsonl(second/'inputs.jsonl',[r])
        count=len(self.calls)
        p.run(second,self.config,self.policy,fail,contract=self.d,modes=('primary',),shared_budget_root=ledger)
        self.assertEqual(len(self.calls),count)
        self.assertGreater(count,0)


if __name__=='__main__':unittest.main()
