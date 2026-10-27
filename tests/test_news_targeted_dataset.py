import copy
import tempfile
import unittest
from pathlib import Path

from experiments.news_processing import bulk_dataset as bulk
from experiments.news_processing import targeted_dataset as target


class TargetedDatasetTests(unittest.TestCase):
    def setUp(self):
        self.article={'id':'T1','date':'2026-10-20','title':'原油上涨',
                      'text':'国际原油价格上涨3%。另一家公司计划收购。',
                      'targets':['601857','600276'],'role':'development_train'}
        self.article['packet']=bulk.packet('T1',self.article['text'])
        self.catalog={c:{'name':n,'aliases':[n],'evidence':[{'id':'B'+c+'-1','text':t}]}
                      for c,n,t in [('601857','中国石油','开采和销售原油。'),('600276','恒瑞医药','研发生产抗肿瘤药物。')]}
        self.fact={'id':'T1','scope':'INDUSTRY_SIGNAL','stage':'REPORTED','time_role':'CURRENT',
                   'subject_name':'','subject_code':'','news_ids':['T1-1'],'pairs':[]}

    def test_fact_teacher_never_sees_targets_company_profiles_or_labels(self):
        a={**self.article,'fact':self.fact,'expected':'DIRECT','review_label':'NONE'}
        p=target.teacher_packet('facts_primary',[a],self.catalog)
        self.assertEqual(p,target.teacher_packet('facts_primary',[a],{}))
        for key in ['601857','600276','DIRECT','NONE','fact','companies','targets','expected']:
            self.assertNotIn(key,str(p))

    def test_blind_fact_review_gets_interval_but_no_primary_fact_verdict(self):
        a={**self.article,'fact':self.fact}
        p=target.teacher_packet('facts_review',[a],self.catalog)
        self.assertEqual(p['articles'][0]['selected_event_ids'],['T1-1'])
        for key in ['scope','stage','time_role','subject_name']:
            self.assertNotIn(key,str(p))

    def test_hallucinated_actor_code_cannot_be_borrowed_from_target_catalog(self):
        raw={**self.fact,'news_span':['T1-1','T1-1'],'subject_code':'601857'}
        with self.assertRaisesRegex(ValueError,'Actor is absent'):
            target.check_fact(self.article,raw)

    def test_review_cannot_switch_selected_event_even_with_same_scope(self):
        raw={**self.fact,'news_span':['T1-2','T1-2']}
        with self.assertRaisesRegex(ValueError,'changed selected event'):
            target.check_fact({**self.article,'fact':self.fact},raw,review=True)

    def test_relation_errors_are_isolated_per_target_and_never_become_none(self):
        a={**self.article,'fact':self.fact}
        raw={'id':'T1','pairs':[{'code':'601857','relation':'DIRECT','business_ids':['B601857-1'],
                                'conditions':[],'why':'原油售价暴露。'},None]}
        answer,errors=target.check_relations(a,self.catalog,raw)
        self.assertEqual([p['code'] for p in answer['pairs']],['601857'])
        self.assertIn('600276',errors)
        raw['pairs'][0]['business_ids']=['B600276-1']
        answer,errors=target.check_relations(a,self.catalog,raw)
        self.assertEqual(answer['pairs'],[])
        self.assertEqual(set(errors),{'601857','600276'})

    def test_label_verdicts_are_hidden_from_relation_reviewer(self):
        a={**self.article,'fact':self.fact,'primary_relation':'DISTANT','expected':'NONE'}
        p=target.teacher_packet('relations_review',[a],self.catalog)
        self.assertNotIn('primary_relation',str(p));self.assertNotIn('DISTANT',str(p))
        self.assertEqual(p['articles'][0]['fact']['scope'],'INDUSTRY_SIGNAL')

    def test_eval_company_or_future_material_is_rejected_from_training(self):
        declaration={'training_company_codes':['601857','600276'],
                     'training_publication_window':['2026-09-28T00:00:00+08:00','2026-10-26T23:59:59+08:00']}
        target.check_training_material(self.article,declaration)
        for changed in [{'targets':['002594']},{'date':'2026-10-27'},{'role':'evaluation_only'}]:
            with self.assertRaises(ValueError):
                target.check_training_material({**self.article,**changed},declaration)

    def test_reference_class_is_not_put_into_student_inputs(self):
        a={**self.article,'group_id':'g','parent_id':'p','source_level':'single_provider_unverified'}
        catalog=copy.deepcopy(self.catalog)
        catalog['601857']['company']={'code':'601857','products':['开采和销售原油。']}
        pair={'code':'601857','relation':'DIRECT','business_ids':['B601857-1'],'conditions':[],'why':'原油售价暴露。'}
        sample=bulk.example(a,catalog,self.fact,pair,reviewed=True)
        self.assertNotIn('DIRECT',sample['messages'][1]['content'])
        self.assertNotIn('INDUSTRY_SIGNAL',sample['messages'][1]['content'])
        self.assertFalse(sample['independent_test']);self.assertFalse(sample['human_verified'])

    def test_copy_screen_catches_reprints_across_punctuation_and_dates(self):
        first=target.text_fingerprint('公司今日公告：新建动力电池工厂，产能提升20%。')
        other=target.text_fingerprint('公司今日公告，新建动力电池工厂产能提升20%！')
        self.assertGreater(target.overlap_score(first,other),0.8)
        self.assertEqual(target.overlap_score(first,target.text_fingerprint('上海港新增集装箱码头泊位')) ,0)

    def test_closed_phase_cannot_be_reopened_even_under_a_child_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'closed.json').write_text('{}')
            for candidate in [root,root/'recovered']:
                with self.assertRaisesRegex(ValueError,'phase is closed'):
                    target.run_stage(candidate,'facts_primary',[],{}, {},cap=1)
            self.assertFalse((root/'facts_primary').exists())

    def test_training_label_run_blocks_eval_material_before_network_or_reservation(self):
        from experiments.news_processing.storage import write_json
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            write_json(root/'declaration.json',{'training_company_codes':['601857'],
                'training_publication_window':['2026-09-28','2026-10-26']})
            with self.assertRaisesRegex(ValueError,'Non-training role'):
                target.run_stage(root,'facts_primary',[{**self.article,'role':'evaluation_only'}],{}, {},cap=1)
            self.assertFalse((root/'facts_primary').exists())

    def test_array_normalization_preserves_all_values_and_does_not_guess_labels(self):
        import json
        values=[{'id':'T1','relation':'UNKNOWN'},{'id':'T1','relation':'NONE'}]
        items,normalized=target.response_items(json.dumps(values))
        self.assertEqual(items,values);self.assertTrue(normalized)
        self.assertEqual(target.response_items(json.dumps({'items':values})),(values,False))
        for bad in ['null','{}','{"items":null}']:
            with self.assertRaises(ValueError):target.response_items(bad)

    def test_evaluation_gate_requires_fixed_weights_and_exact_sealed_material(self):
        from experiments.news_processing.storage import write_json,write_jsonl
        from experiments.news_processing.evidence_pipeline import sha
        a={**self.article,'role':'evaluation_only','parent_id':'p','group_id':'g'}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with self.assertRaises(FileNotFoundError):target.evaluation_gate(root,[a])
            write_json(root/'weights_fixed_before_evaluation.json',{'checkpoint_sha256':'a'*64,'fixed_at_unix':1})
            write_json(root/'evaluation_declaration.json',{'only_target_codes':['601857','600276'],
                'publication_window':['2026-10-20','2026-10-21']})
            write_jsonl(root/'evaluation_candidates_sealed.jsonl',[a])
            write_json(root/'company_catalog_evaluation.json',self.catalog)
            write_json(root/'evaluation_input_freeze.json',{'checkpoint_sha256':'a'*64,
                'files':{name:sha(root/name) for name in ['evaluation_declaration.json','evaluation_candidates_sealed.jsonl','company_catalog_evaluation.json','weights_fixed_before_evaluation.json']}})
            self.assertEqual(target.evaluation_gate(root,[a]),'a'*64)
            with self.assertRaisesRegex(ValueError,'Business input differs'):
                target.run_stage(root,'relations_review',[{**a,'fact':self.fact}],{}, {},cap=1,evaluation=True)
            for changed in [{'role':'development_train'},{'targets':['002594']},{'text':'different news'}]:
                with self.assertRaises(ValueError):target.evaluation_gate(root,[{**a,**changed}])
            write_json(root/'weights_fixed_before_evaluation.json',{'checkpoint_sha256':'b'*64,'fixed_at_unix':1})
            with self.assertRaisesRegex(ValueError,'checkpoint changed'):target.evaluation_gate(root,[a])
            self.assertFalse((root/'facts_primary').exists())

    def test_evaluation_freeze_cannot_omit_required_inputs(self):
        from experiments.news_processing.storage import write_json
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            write_json(root/'weights_fixed_before_evaluation.json',{'checkpoint_sha256':'a'*64,'fixed_at_unix':1})
            for files in [{},[],{'weights_fixed_before_evaluation.json':'irrelevant'}]:
                write_json(root/'evaluation_input_freeze.json',{'checkpoint_sha256':'a'*64,'files':files})
                with self.assertRaisesRegex(ValueError,'Incomplete evaluation freeze'):
                    target.evaluation_gate(root,[])

    def test_evaluation_references_are_projectable_for_inference_but_never_trainable(self):
        from experiments.news_processing.silver_trial import adapt,question_schema
        from experiments.news_processing.contract import digest
        a={**self.article,'role':'evaluation_only','group_id':'eval-g','parent_id':'p','source_level':'provider_unverified'}
        row=bulk.example(a,self.catalog,self.fact,None,reviewed=True)
        # Even an otherwise valid silver tier must respect its protected role.
        with self.assertRaisesRegex(ValueError,'cannot enter training'):adapt(row,question_schema(expanded=True,staged=True))
        row['label_tier']='evaluation_only_AI_reference'
        row['sha256']=digest({k:v for k,v in row.items() if k!='sha256'})
        with self.assertRaisesRegex(ValueError,'cannot enter training'):adapt(row,question_schema(expanded=True,staged=True))
        state=adapt(row,question_schema(expanded=True,staged=True),for_evaluation=True)
        self.assertEqual(state['expected'],{'fact_scope':'INDUSTRY_SIGNAL'})
        self.assertNotIn('INDUSTRY_SIGNAL',str(state['state']))
