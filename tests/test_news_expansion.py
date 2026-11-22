import copy
import hashlib
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch
import urllib.error

from experiments.news_processing import expansion
from tests.test_news_processing import record


def answer():
    return {'own_event': 'NO', 'own_quote': '', 'external_signal': 'YES',
            'signal_kind': 'COST', 'signal_quote': '普通中板价格上涨',
            'transmission': 'DIRECT', 'company_quote': '钢材',
            'mechanism': '钢材价格影响工程机械投入成本', 'missing_conditions': []}


class ExpansionTests(unittest.TestCase):
    def test_external_signal_and_company_own_event_are_separate(self):
        self.assertEqual(expansion.validate_label(record(), answer())['transmission'], 'DIRECT')
        r = record(); r['state']['news']['text'] = '三一重工签订工程机械销售合同。'
        a = answer(); a.update(own_event='YES', own_quote='三一重工签订工程机械销售合同',
                               external_signal='NO', signal_kind='NONE', signal_quote='',
                               transmission='NONE', company_quote='')
        self.assertEqual(expansion.validate_label(r, a)['own_event'], 'YES')
        a['transmission'] = 'DIRECT'
        with self.assertRaises(ValueError): expansion.validate_label(r, a)

    def test_conditional_transmission_requires_conditions_and_business_evidence(self):
        a = answer(); a['transmission'] = 'CONDITIONAL'
        with self.assertRaises(ValueError): expansion.validate_label(record(), a)
        a['missing_conditions'] = ['待确认所需钢材品类']
        self.assertEqual(expansion.validate_label(record(), a)['human_verified'], False)
        a['company_quote'] = '钢材和汽车零件'
        with self.assertRaises(ValueError): expansion.validate_label(record(), a)

    def test_missing_signal_or_fabricated_quote_is_rejected(self):
        for changes in ({'signal_quote': '统计显示铜价上涨'},
                        {'external_signal': 'UNKNOWN'}, {'signal_kind': 'NONE'},
                        {'own_event': 'YES', 'own_quote': '三一重工'}):
            a = answer(); a.update(changes)
            with self.assertRaises(ValueError): expansion.validate_label(record(), a)

    def test_unknowns_are_retained_as_weak_labels_not_forced_to_none(self):
        a = answer(); a.update(own_event='UNKNOWN', external_signal='UNKNOWN',
                               signal_kind='UNKNOWN', signal_quote='', transmission='UNKNOWN', company_quote='')
        self.assertEqual(expansion.validate_label(record(), a)['external_signal'], 'UNKNOWN')

    def test_quote_offsets_preserve_table_and_original_context(self):
        r = record(); r['state']['news']['market_rows'] = [['产品', '单位', '涨幅'], ['钢材', '吨', '3.0%']]
        a = answer(); a['signal_quote'] = '钢材 | 吨 | 3.0%'
        checked = expansion.validate_label(r, a)
        lo, hi = checked['quote_offsets']['signal_quote']
        self.assertEqual(expansion.news_visible(r)[lo:hi], '钢材 | 吨 | 3.0%')

    def test_model_hash_does_not_include_teacher_labels(self):
        r = record(); h = expansion.input_hash(r)
        r['expected'] = answer(); r['reviewer_note'] = 'SECRET LABEL'
        self.assertEqual(expansion.input_hash(r), h)
        r['state']['news']['text'] += ' 后续变化'
        self.assertNotEqual(expansion.input_hash(r), h)

    def test_same_source_body_and_event_cannot_cross_roles(self):
        a = record(); a.update(split='train', body_sha256='body')
        for change in ({'group_id': 'other'}, {'source': {'url': 'https://other.org/source'}},
                       {'source': {'url': 'https://other.org/source'}, 'body_sha256': 'other'}):
            b = copy.deepcopy(a); b.update(id='other', split='validation', **change)
            with self.assertRaises(ValueError): expansion.validate_roles([a, b])

    def test_synthetic_children_cannot_enter_calibration_or_detach_parent(self):
        a = record(); a.update(split='calibration', synthetic=True, parent_group_id='steel')
        with self.assertRaises(ValueError): expansion.validate_roles([a])
        a.update(split='train', parent_group_id='different')
        with self.assertRaises(ValueError): expansion.validate_roles([a])

    def test_frozen_input_edit_is_rejected_before_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'inputs.jsonl'; p.write_text('original')
            expected = hashlib.sha256(b'original').hexdigest()
            expansion.assert_file_hash(p, expected)
            p.write_text('edited')
            with self.assertRaises(ValueError): expansion.assert_file_hash(p, expected)

    def test_original_source_tamper_is_rejected_before_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'raw').mkdir(); p=root/'raw'/'document.bin'; p.write_bytes(b'original source')
            (root/'materials.json').write_text(json.dumps([{'raw_file':'raw/document.bin','quality':{'body':'ready'},
                'source':{'raw_sha256':hashlib.sha256(b'original source').hexdigest()}}]))
            expansion.verify_sources(root)
            p.write_bytes(b'changed source')
            with self.assertRaises(ValueError): expansion.verify_sources(root)

    def test_evidence_validation_cannot_promote_a_pending_body(self):
        r = record(); r['quality']['body'] = 'pending'
        self.assertFalse(expansion.eligible(r))
        r['quality']['body'] = 'ready'; self.assertTrue(expansion.eligible(r))

    def test_body_and_quote_identity_changes_invalidate_cache(self):
        r = record(); identity = {'model':'test','endpoint':'https://example.org'}
        a = expansion.cache_identity(r, identity, 'primary')
        self.assertNotEqual(a, expansion.cache_identity(r, identity, 'audit'))
        r['state']['company']['upstream'].append('铜材')
        self.assertNotEqual(a, expansion.cache_identity(r, identity, 'primary'))

    def test_journaled_label_resume_does_not_repeat_paid_requests(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self):
                return json.dumps({'usage':{'total_tokens':20},'choices':[{'finish_reason':'stop',
                    'message':{'content':json.dumps({'labels':[{'id':'steel__600031',**answer()}]})}}]}).encode()
        r=record(); r['split']='train'
        policy={'max_attempts':1,'token_soft_stop':100,'batch_size':1,'workers':1,'attempts_per_batch':1,'max_output_tokens':1000}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); config=root/'private.json'
            config.write_text(json.dumps({'model':'test','base_url':'https://example.org/v1','api_key':'DO_NOT_ARCHIVE_KEY'}))
            with patch('urllib.request.urlopen',return_value=Response()):
                summary=expansion.label_batch(root/'output',[r],config,policy,modes=('primary',))
            with patch('urllib.request.urlopen',side_effect=AssertionError('Unexpected repeated API request')):
                resumed=expansion.label_batch(root/'output',[r],config,policy,modes=('primary',))
            self.assertEqual(resumed['attempts'],1); self.assertEqual(summary['observed_tokens'],20)
            result=json.loads((root/'output'/'primary.jsonl').read_text())
            self.assertTrue(result['evidence_valid']); self.assertFalse(result['human_verified'])
            self.assertEqual(resumed['coverage']['primary']['valid_evidence'],1)
            self.assertEqual(resumed['coverage']['primary']['missing_ids'],[])
            self.assertNotIn('DO_NOT_ARCHIVE_KEY',''.join(p.read_text() for p in (root/'output').iterdir()))
            r['state']['news']['text']+='内容改变'
            with self.assertRaises(ValueError): expansion.label_batch(root/'output',[r],config,policy,modes=('primary',))

    def test_provider_auth_failure_is_journaled_and_stops_retrying(self):
        r=record(); r['split']='train'
        policy={'max_attempts':10,'token_soft_stop':100,'batch_size':1,'workers':1,'attempts_per_batch':2,'max_output_tokens':1000}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); config=root/'private.json'
            config.write_text(json.dumps({'model':'test','base_url':'https://example.org/v1','api_key':'secret'}))
            with patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError('https://example.org',401,'SECRET ERROR',None,None)):
                summary=expansion.label_batch(root/'out',[r],config,policy,modes=('primary','audit'))
            self.assertEqual(summary['attempts'],1)
            self.assertEqual(summary['coverage']['primary']['missing_ids'],['steel__600031'])
            failure=json.loads((root/'out'/'failures.jsonl').read_text())
            self.assertEqual(failure['http_status'],401)
            self.assertNotIn('SECRET ERROR',(root/'out'/'failures.jsonl').read_text())

    def test_review_status_cannot_promote_unverified_or_stale_teacher_answers(self):
        r=record(); r['split']='train'; identity={'model':'test','endpoint':'https://example.org'}
        def labeled(mode):
            return {'id':r['id'],'label':answer(),'evidence_valid':True,'model_identity':identity,
                    'input_sha256':expansion.input_hash(r),'cache_key':expansion.cache_identity(r,identity,mode),
                    'prompt_sha256':expansion.digest(expansion.PROMPTS[mode])}
        a,b=labeled('primary'),labeled('audit')
        self.assertEqual(expansion.review_status(r,a,b,identity),'weak_agreement_pending_semantic_review')
        b['input_sha256']='stale'
        self.assertEqual(expansion.review_status(r,a,b,identity),'pending_label_validation')
        self.assertEqual(expansion.review_status(r,None,None,identity),'awaiting_label')
        r['quality']['body']='pending'
        self.assertEqual(expansion.review_status(r,a,b,identity),'pending_source')

    def test_review_page_escapes_source_script_injection(self):
        r=record(); r['state']['news']['text']='</script><script>ATTACK</script>'
        page=expansion.render_review([{'record':r,'status':'awaiting_label','primary':None,'audit':None}],{})
        self.assertNotIn('</script><script>ATTACK',page)
        self.assertIn('\\u003c/script\\u003e',page)


if __name__ == '__main__': unittest.main()
