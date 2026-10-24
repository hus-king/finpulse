import json
import tempfile
import unittest
from pathlib import Path

from experiments.news_processing.evidence_pipeline import run
from experiments.news_processing.evidence_replay import replay
from experiments.news_processing.storage import read_jsonl, write_jsonl
from tests.test_news_processing import record


class EvidenceReplayTests(unittest.TestCase):
    def test_code_id_replay_keeps_provenance_and_retains_original_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            source,dest=Path(temp)/'source',Path(temp)/'replay';source.mkdir()
            r=record();r['article_id']='steel';write_jsonl(source/'inputs.jsonl',[r])
            calls=[]
            def request(url,payload,key):
                wire=json.loads(payload['messages'][1]['content']);calls.append(wire)
                if isinstance(wire,list):
                    a=wire[0]['article']
                    answer={'id':a['id'],'facts':[{'fact_id':'F1','scope':'INDUSTRY_SIGNAL','kind':'PRICE','stage':'REPORTED',
                        'subject_name':'','subject_code':'','news_evidence_ids':[a['evidence'][0]['id']],
                        'subject_evidence_ids':[],'description':'钢价'}]}
                else:answer={'id':'600031','own_event':'NO','own_fact_ids':[],'links':[
                    {'fact_id':'F1','transmission':'NONE','price_role':'NOT_APPLICABLE','business_evidence_ids':[],
                     'mechanism':'待业务复核','missing_conditions':[]}]}
                return {'usage':{'total_tokens':10},'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'answers':[answer]})}}]}
            run(source,{'model':'fake','base_url':'https://example.test','api_key':'secret'},
                {'max_attempts':8,'token_soft_stop':50000,'attempts_per_batch':2,'workers':1,'batch_size':2,'max_output_tokens':4000},request)
            before={str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()}
            summary=replay(source,dest)
            self.assertEqual(summary['both_structurally_valid'],1);self.assertEqual(summary['new_provider_calls'],0)
            self.assertFalse(summary['independent_test'])
            answer=read_jsonl(dest/'teacher'/'pairs_primary.jsonl')[0]['answer']
            self.assertEqual(answer['returned_id'],'600031');self.assertFalse(answer['human_verified'])
            self.assertEqual(before,{str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()})
            with self.assertRaises(ValueError):replay(source,dest)


if __name__=='__main__':unittest.main()
