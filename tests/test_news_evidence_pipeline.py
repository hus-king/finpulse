import copy
import json
import tempfile
import unittest
from pathlib import Path

from experiments.news_processing import evidence
from experiments.news_processing.storage import read_jsonl, write_json, write_jsonl
from tests.test_news_processing import record


class EvidencePipelineTests(unittest.TestCase):
    def setUp(self):
        from experiments.news_processing import evidence_pipeline as pipeline
        self.p = pipeline
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.r = record(); self.r['article_id'] = 'steel'
        write_jsonl(self.root/'inputs.jsonl', [self.r])
        self.policy = {'max_attempts': 8, 'token_soft_stop': 50000, 'attempts_per_batch': 2,
                       'workers': 1, 'batch_size': 2, 'max_output_tokens': 4000}
        self.config = {'model': 'fake', 'base_url': 'https://example.test/v1', 'api_key': 'secret'}
        self.calls = []

    def request(self, url, payload, key):
        wire = json.loads(payload['messages'][1]['content'])
        items = [{'company':{**c,'id':c['pair_id']}} for c in wire['targets']] if isinstance(wire,dict) else wire
        self.calls.append(items)
        if 'article' in items[0]:
            a = items[0]['article']; eid = a['evidence'][0]['id']
            labels = [{'id': a['id'], 'facts': [{'fact_id': 'F1', 'scope': 'INDUSTRY_SIGNAL', 'kind': 'PRICE',
                       'stage': 'REPORTED', 'subject_name': '', 'subject_code': '', 'description': '价格变化',
                       'news_evidence_ids': [eid], 'subject_evidence_ids': []}]}]
        else:
            labels = [{'id': x['company']['id'], 'own_event': 'NO', 'own_fact_ids': [], 'links': [
                {'fact_id': 'F1', 'transmission': 'NONE', 'price_role': 'NOT_APPLICABLE',
                 'business_evidence_ids': [], 'mechanism': '待业务复核', 'missing_conditions': []}]} for x in items]
        return {'usage': {'total_tokens': 10}, 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'answers': labels})}}]}

    def run_pipeline(self, request=None):
        return self.p.run(self.root, self.config, self.policy, request=request or self.request)

    def test_shared_article_extraction_and_no_calls_on_resume(self):
        r = copy.deepcopy(self.r); r['id'] = 'steel__other'; r['target_code'] = '600276'
        write_jsonl(self.root/'inputs.jsonl', [self.r, r])
        result = self.run_pipeline()
        self.assertEqual(len(self.calls), 4)  # two article calls, two pair batches
        self.assertEqual(result['pairs'], 2); self.assertEqual(result['both_structurally_valid'], 2)
        self.run_pipeline(); self.assertEqual(len(self.calls), 4)
        self.assertNotIn('secret', ''.join(x.read_text() for x in self.root.rglob('*.json')))

    def test_phase_dependency_tampering_prevents_resume(self):
        self.run_pipeline()
        path = self.root/'teacher'/'facts_primary.jsonl'
        path.write_text(path.read_text()+'\n')
        with self.assertRaises(ValueError): self.run_pipeline()

    def test_failed_extraction_keeps_all_pairs_pending(self):
        def fail(*_): raise RuntimeError('provider unavailable')
        result = self.run_pipeline(fail)
        self.assertEqual(result['pairs'], 1); self.assertEqual(result['both_structurally_valid'], 0)
        self.assertEqual(len(read_jsonl(self.root/'review_queue.jsonl')), 1)
        self.assertEqual(len(read_jsonl(self.root/'teacher'/'attempts.jsonl')), 4)
        self.run_pipeline(fail)
        self.assertEqual(len(read_jsonl(self.root/'teacher'/'attempts.jsonl')), 4)

    def test_truncation_is_pending_and_raw_is_retained(self):
        def truncated(*args):
            raw = self.request(*args); raw['choices'][0]['finish_reason'] = 'length'; return raw
        result = self.run_pipeline(truncated)
        self.assertEqual(result['both_structurally_valid'], 0)
        self.assertTrue(read_jsonl(self.root/'teacher'/'raw_answers.jsonl'))

    def test_changed_inputs_and_policy_are_rejected(self):
        self.run_pipeline()
        changed = copy.deepcopy(self.r); changed['state']['news']['text'] += '修改'
        write_jsonl(self.root/'inputs.jsonl', [changed])
        with self.assertRaises(ValueError): self.run_pipeline()

    def test_hard_budget_blocks_requests_before_they_are_sent(self):
        self.policy['token_hard_cap']=1
        result=self.run_pipeline()
        self.assertEqual(len(self.calls),0);self.assertEqual(result['pending_label'],1)

    def test_unobserved_failed_request_keeps_budget_reserved_on_resume(self):
        self.policy['token_hard_cap']=14000
        self.calls=[]
        def fail(*args):self.calls.append(args);raise RuntimeError('uncertain billable request')
        self.run_pipeline(fail)
        self.assertEqual(len(self.calls),1)
        self.run_pipeline(fail);self.assertEqual(len(self.calls),1)

    def test_pair_wire_shares_news_and_omits_resolved_quote_duplicates(self):
        a=evidence.article_packet('steel',self.r['state']['news'])
        f={'facts':[{'fact_id':'F1','resolved_news_evidence':[{'quote':'duplicate'}]}]}
        items=[{'news':a,'facts':f,'company':evidence.company_packet(self.r)}]*2
        wire=self.p.wire('pairs',items)
        self.assertNotIn('visible',wire['news']);self.assertNotIn('resolved_news_evidence',wire['facts']['facts'][0])
        self.assertEqual(len(wire['targets']),2)

    def test_semantically_valid_cache_edit_is_detected(self):
        self.run_pipeline()
        path=self.root/'teacher'/'pairs_primary.jsonl'
        rows=read_jsonl(path);rows[0]['answer']['links'][0]['mechanism']='another structurally valid explanation'
        write_jsonl(path,rows)
        with self.assertRaises(ValueError):self.run_pipeline()

    def test_exact_request_code_alias_is_explicit_and_unknown_id_is_rejected(self):
        items=[{'company':evidence.company_packet(self.r)}]
        raw={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'answers':[{'id':'600031'}]})}}]}
        answer=self.p._answers(items,raw)[self.r['id']]
        self.assertEqual(answer['returned_id'],'600031');self.assertIn('id_normalization',answer)
        raw['choices'][0]['message']['content']=json.dumps({'answers':[{'id':'600'}]})
        with self.assertRaises(ValueError):self.p._answers(items,raw)

    def test_request_code_and_different_pair_id_collision_is_rejected(self):
        other=copy.deepcopy(self.r);other['id']='600031';other['target_code']='600276'
        items=[{'company':evidence.company_packet(r)} for r in [self.r,other]]
        raw={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'answers':[{'id':self.r['id']},{'id':'600031'}]})}}]}
        with self.assertRaises(ValueError):self.p._answers(items,raw)


if __name__ == '__main__': unittest.main()
