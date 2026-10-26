import copy
import tempfile
import unittest
from pathlib import Path

from experiments.news_processing.bulk_dataset import packet, validate_item, model_packet, example, reviewed_match, expand_span, export_dataset, source_problem, run, checked_pairs
from experiments.news_processing.evidence_pipeline import sha
from experiments.news_processing.storage import write_json, write_jsonl, read_jsonl
from experiments.news_processing.silver_trial import adapt, question_schema


class BulkDatasetTests(unittest.TestCase):
    def setUp(self):
        self.article={'id':'N1','group_id':'g1','date':'2026-10-20','title':'原油价格上涨',
            'text':'国际原油价格上涨3%。另一家公司宣布收购。市场继续交易。','targets':['601857','600276'],
            'source_level':'provider_text_unverified','parent_id':'parent','role':'development_train'}
        self.profiles={
            '601857':{'code':'601857','name':'中国石油','aliases':['中国石油'],
                'company':{'code':'601857','name':'中国石油','products':['原油及天然气生产和销售。']},
                'evidence':[{'id':'B601857-1','text':'原油及天然气生产和销售。'}]},
            '600276':{'code':'600276','name':'恒瑞医药','aliases':['恒瑞医药'],
                'company':{'code':'600276','name':'恒瑞医药','products':['公司从事药品研发、生产和销售。']},
                'evidence':[{'id':'B600276-1','text':'公司从事药品研发、生产和销售。'}]}}
        self.article['packet']=packet('N1',self.article['text'])
        self.answer={'id':'N1','scope':'INDUSTRY_SIGNAL','subject_name':'','subject_code':'',
            'stage':'REPORTED','time_role':'CURRENT','news_ids':['N1-1'],
            'pairs':[{'code':'601857','relation':'DIRECT','business_ids':['B601857-1'],'conditions':[],'why':'原油销售价格暴露。'},
                     {'code':'600276','relation':'NONE','business_ids':['B600276-1'],'conditions':[],'why':'所给制药业务缺少实质连接。'}]}

    def test_missing_and_duplicate_targets_are_errors_not_none(self):
        for pairs in [self.answer['pairs'][:1],self.answer['pairs'][:1]*2,self.answer['pairs']+[None]]:
            with self.assertRaises(ValueError):validate_item(self.article,self.profiles,{**self.answer,'pairs':pairs})

    def test_foreign_business_evidence_and_unproved_own_are_rejected(self):
        bad=copy.deepcopy(self.answer);bad['pairs'][0]['business_ids']=['B600276-1']
        with self.assertRaises(ValueError):validate_item(self.article,self.profiles,bad)
        bad=copy.deepcopy(self.answer);bad.update(scope='COMPANY_EVENT',subject_name='另一家公司')
        bad['news_ids']=['N1-2']
        bad['pairs'][0]['relation']='OWN'
        with self.assertRaises(ValueError):validate_item(self.article,self.profiles,bad)

    def test_selected_event_cannot_stitch_nonadjacent_spans(self):
        bad=copy.deepcopy(self.answer);bad['news_ids']=['N1-1','N1-3']
        with self.assertRaises(ValueError):validate_item(self.article,self.profiles,bad)

    def test_uncertain_relation_stays_unknown_and_requires_no_fake_conditions(self):
        a=copy.deepcopy(self.answer);a['pairs'][0].update(relation='UNKNOWN',business_ids=[])
        result=validate_item(self.article,self.profiles,a)
        self.assertEqual(result['pairs'][0]['relation'],'UNKNOWN')
        bad=copy.deepcopy(self.answer);bad['pairs'][0]['relation']='DISTANT'
        with self.assertRaises(ValueError):validate_item(self.article,self.profiles,bad)

    def test_model_packet_hides_selection_notes_and_references(self):
        self.article.update(expected='NONE',review_label='DIRECT',mentioned_target_candidates=['601857'])
        p=model_packet([self.article],self.profiles)
        text=str(p)
        for key in ['expected','review_label','mentioned_target_candidates','group_id']:
            self.assertNotIn(key,text)

    def test_export_preserves_weak_tier_and_current_adapter_contract(self):
        checked=validate_item(self.article,self.profiles,self.answer)
        e=example(self.article,self.profiles,checked,checked['pairs'][0],reviewed=False)
        self.assertEqual(e['label_tier'],'provider_AI_weak_candidate')
        self.assertFalse(e['human_verified']);self.assertFalse(e['training_admitted'])
        # Existing trainer requires an explicit supported-silver tier: raw weak labels cannot slip through.
        with self.assertRaises(ValueError):adapt(e,question_schema(expanded=True))
        silver=example(self.article,self.profiles,checked,checked['pairs'][0],reviewed=True)
        self.assertEqual(adapt(silver,question_schema(expanded=True))['expected'],{'event_relation':'DIRECT'})

    def test_agreeing_labels_for_a_different_event_do_not_pass_review(self):
        a=validate_item(self.article,self.profiles,self.answer)
        b=copy.deepcopy(a);b['news_ids']=['N1-2'];b['subject_name']='另一家公司'
        self.assertFalse(reviewed_match(a,a['pairs'][0],b,b['pairs'][0]))
        b=copy.deepcopy(a);b['news_ids']=['N1-1','N1-2']
        self.assertFalse(reviewed_match(a,a['pairs'][0],b,b['pairs'][0]))

    def test_span_preserves_intervening_text_and_rejects_reverse_or_foreign_bounds(self):
        raw={**self.answer,'news_span':['N1-1','N1-3']};raw.pop('news_ids')
        result=expand_span(self.article,raw)
        self.assertEqual(result['news_ids'],['N1-1','N1-2','N1-3'])
        spans={x['id']:x['text'] for x in self.article['packet']['evidence']}
        self.assertIn('另一家公司宣布收购。',''.join(spans[i] for i in result['news_ids']))
        for bounds in [['N1-3','N1-1'],['N1-1','N2-3']]:
            with self.assertRaises(ValueError):expand_span(self.article,{**raw,'news_span':bounds})

    def test_export_requires_review_agreement_and_preserves_unreviewed_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);phase=root/'phase';phase.mkdir();(phase/'teacher').mkdir()
            write_jsonl(phase/'articles.jsonl',[self.article]);write_json(phase/'company_catalog.json',self.profiles)
            write_jsonl(phase/'teacher'/'labels.jsonl',[{'id':'N1','answer':self.answer,'error':None}])
            review=copy.deepcopy(self.answer);review['pairs'][1]['relation']='UNKNOWN'
            reviews=root/'reviews.jsonl';write_jsonl(reviews,[{'id':'N1','answer':review,'error':None,'provider':'test_only'}])
            out=root/'export';result=export_dataset(out,[phase],reviews)
            self.assertEqual(result['valid_company_pairs'],2)
            self.assertEqual(result['review_agreed_company_pairs'],1)
            weak=read_jsonl(out/'weak_candidates.jsonl');silver=read_jsonl(out/'silver_candidates.jsonl')
            self.assertEqual(len(weak),3);self.assertEqual(len(silver),2)
            for row in weak+silver:
                self.assertFalse(row['human_verified']);self.assertFalse(row['training_admitted'])
                self.assertIn('phase_labels_sha256',row['lineage'])
            for row in silver:adapt(row,question_schema(expanded=True))
            self.assertEqual(read_jsonl(out/'review_comparisons.jsonl')[1]['status'],'disagreement')
            with self.assertRaises(ValueError):export_dataset(out,[phase],reviews)

    def test_navigation_body_and_title_only_event_remain_pending(self):
        a=copy.deepcopy(self.article);a['packet']=packet('N1',a['title']+'\n'+a['text'])
        self.assertEqual(source_problem(a,{**self.answer,'news_ids':['N1-1']}),'selected_event_title_only')
        a['text']='正文。\n- 用户123:想清仓\n- 小明:今天走势不错\n- 10-20 某某 600000 4.48'
        self.assertEqual(source_problem(a,{**self.answer,'news_ids':['N1-1','N1-2']}),'navigation_dominated_saved_text')
        self.assertIsNone(source_problem(self.article,self.answer))

    def test_label_run_rejects_different_executable_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'code_snapshot').mkdir()
            name='code_snapshot/bulk_dataset.py';(root/name).write_text('different executed code')
            write_json(root/'protocol.json',{'frozen_files':{name:sha(root/name)}})
            with self.assertRaisesRegex(ValueError,'Executable source differs'):
                run(root,{})

    def test_pair_isolation_preserves_other_target_and_never_repairs_bad_evidence(self):
        item=copy.deepcopy(self.answer);item['pairs'][0]['business_ids']=['B600276-1']
        result=checked_pairs(self.article,self.profiles,item)
        self.assertIsNone(result['601857']['answer'])
        self.assertEqual(result['601857']['error'],'Foreign business evidence')
        self.assertEqual(result['600276']['answer']['pairs'][0]['relation'],'NONE')
        item['stage']='BAD'
        self.assertTrue(all(x['answer'] is None for x in checked_pairs(self.article,self.profiles,item).values()))

    def test_export_pair_review_does_not_admit_invalid_sibling(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);phase=root/'phase';phase.mkdir();(phase/'teacher').mkdir()
            write_jsonl(phase/'articles.jsonl',[self.article]);write_json(phase/'company_catalog.json',self.profiles)
            write_jsonl(phase/'teacher/labels.jsonl',[{'id':'N1','answer':self.answer,'error':None}])
            review=copy.deepcopy(self.answer);review['pairs'][0]['business_ids']=[]
            reviews=root/'reviews.jsonl';write_jsonl(reviews,[{'id':'N1','answers_by_target':checked_pairs(self.article,self.profiles,review)}])
            out=root/'export';result=export_dataset(out,[phase],reviews)
            self.assertEqual(result['review_valid_company_pairs'],1)
            self.assertEqual(result['review_agreed_company_pairs'],1)
            self.assertEqual(read_jsonl(out/'review_comparisons.jsonl')[0]['status'],'review_invalid')
            self.assertEqual([r['id'] for r in read_jsonl(out/'silver_candidates.jsonl')],['N1:scope','N1__600276'])

    def test_missing_event_is_saved_for_selection_review_without_sft_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);phase=root/'phase';phase.mkdir();(phase/'teacher').mkdir()
            event=copy.deepcopy(self.answer);event.update(scope='UNKNOWN',news_ids=[])
            for pair in event['pairs']:pair.update(relation='UNKNOWN',business_ids=[])
            write_jsonl(phase/'articles.jsonl',[self.article]);write_json(phase/'company_catalog.json',self.profiles)
            write_jsonl(phase/'teacher/labels.jsonl',[{'id':'N1','answer':event,'error':None}])
            out=root/'export';result=export_dataset(out,[phase])
            self.assertEqual(result['selection_pending_articles'],1)
            self.assertEqual(result['valid_company_pairs'],2)
            self.assertEqual(read_jsonl(out/'weak_candidates.jsonl'),[])
            self.assertEqual(read_jsonl(out/'selection_pending.jsonl')[0]['reason'],'no_selected_event')


if __name__=='__main__':unittest.main()
