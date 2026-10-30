import copy
import unittest

from experiments.news_processing import bulk_dataset as bulk, path_first_dataset as paths


class PathFirstDatasetTests(unittest.TestCase):
    def setUp(self):
        self.a={'id':'P1','date':'2026-09-09','title':'铁矿石涨价','text':'铁矿石价格上涨3%。','targets':['A','S']}
        self.a['packet']=bulk.packet('P1',self.a['text'])
        self.a['fact']={'id':'P1','scope':'INDUSTRY_SIGNAL','stage':'REPORTED','time_role':'CURRENT',
            'subject_name':'','subject_code':'','news_ids':['P1-1'],'focus_quote':self.a['text'],'focus_product':'铁矿石','pairs':[]}
        source={'url':'https://example.com/report.pdf','sha256':'a'*64}
        self.c={'A':{'name':'设备企业','aliases':['设备企业'],'primary_profile_source':source,
                     'evidence':[{'id':'BA','text':'本公司采购钢材制造挖掘机。','field':'upstream'}]},
                'S':{'name':'钢厂','aliases':['钢厂'],'primary_profile_source':source,
                     'evidence':[{'id':'BS','text':'公司生产钢材。','field':'products'}],
                     'industry_evidence':[{'id':'GS','text':'钢铁行业主要原料铁矿石价格下降，降低生产成本。','field':'industry_background'}]}}
        self.a['industry_context']=[{'id':'I'+q['id'],'evidence_id':q['id'],'company_code':'S','company_name':'钢厂',
            'field':q['field'],'text':q['text'],'source_url':source['url'],'source_sha256':source['sha256'],
            'published_date':'2026-03-31','as_of':'2025-12-31'} for q in self.c['S']['evidence']+self.c['S']['industry_evidence']]
        self.p={'code':'A','family':'FUNDAMENTAL','source_product':'铁矿石','endpoint_product':'钢材',
                'endpoint_role':'INPUT_COST','business_ids':['BA'],'conditions':['铁矿石到钢材采购价的传导未确认'],
                'missing_axes':['TRANSMISSION'],'reason':'原料价格经钢材影响设备投入成本，传导仍待确认。',
                'intermediates':[{'input_product':'铁矿石','product':'钢材','edge_kind':'INPUT_OUTPUT',
                                  'news_ids':[],'bridge_ids':['IGS','IBS'],'business_ids':['BA']}]}

    def test_ai_material_path_yields_distant_without_an_ai_class_vote(self):
        p=paths.derive_pair(self.a,self.c,self.p)
        self.assertEqual(p['relation'],'DISTANT');self.assertEqual(p['path_basis'],'POTENTIAL_TRANSMISSION')
        self.assertEqual(self.p['family'],'FUNDAMENTAL');self.assertNotIn('relation',self.p)

    def test_an_unproven_ore_to_equipment_path_cannot_be_called_one_hop(self):
        with self.assertRaisesRegex(ValueError,'No direct product'):
            paths.derive_pair(self.a,self.c,{**self.p,'intermediates':[]})

    def test_one_hop_grade_uncertainty_and_observed_exposure_remain_distinct(self):
        a=copy.deepcopy(self.a);a['fact']['focus_quote']='钢材价格上涨。';a['fact']['focus_product']='钢材'
        p={**self.p,'source_product':'钢材','intermediates':[],'missing_axes':['GRADE']}
        self.assertEqual(paths.derive_pair(a,self.c,p)['relation'],'CONDITIONAL')
        self.assertEqual(paths.derive_pair(a,self.c,{**p,'missing_axes':[],'conditions':[]})['relation'],'DIRECT')

    def test_profit_endpoints_and_unrelated_portfolio_words_are_not_material_links(self):
        p=copy.deepcopy(self.p);p['intermediates'][0]['product']='利润'
        with self.assertRaises(ValueError):paths.derive_pair(self.a,self.c,p)
        c=copy.deepcopy(self.c);c['S']['industry_evidence'][0]['text']='本公司同时投资铁矿石基金和钢材基金。'
        a=copy.deepcopy(self.a);a['industry_context'][1]['text']=c['S']['industry_evidence'][0]['text']
        with self.assertRaisesRegex(ValueError,'adjacent business link'):paths.derive_pair(a,c,self.p)

    def test_target_material_can_be_cited_but_needs_separate_conversion_evidence(self):
        p=copy.deepcopy(self.p);p['intermediates'][0]['bridge_ids']=[]
        with self.assertRaisesRegex(ValueError,'adjacent business link'):paths.derive_pair(self.a,self.c,p)
        p['intermediates'][0]['bridge_ids']=['IBS','IGS']
        self.assertEqual(paths.derive_pair(self.a,self.c,p)['relation'],'DISTANT')

    def test_two_inputs_in_the_same_raw_material_list_do_not_prove_conversion(self):
        a=copy.deepcopy(self.a);c=copy.deepcopy(self.c);p=copy.deepcopy(self.p)
        c['A']['evidence'][0]['text']='本公司采购焦炭制造化工产品。'
        c['S']['industry_evidence'][0]['text']='该企业同时采购铁矿石和焦炭等原料。'
        a['industry_context'][1]['text']=c['S']['industry_evidence'][0]['text']
        p['endpoint_product']='焦炭';p['intermediates'][0]['product']='焦炭'
        p['intermediates'][0]['bridge_ids']=['IGS']
        with self.assertRaisesRegex(ValueError,'adjacent business link'):
            paths.derive_pair(a,c,p)

    def test_foreign_evidence_and_hiding_a_shorter_exposure_fail(self):
        p=copy.deepcopy(self.p);p['intermediates'][0]['bridge_ids']=['foreign']
        with self.assertRaisesRegex(ValueError,'Foreign'):paths.derive_pair(self.a,self.c,p)
        c=copy.deepcopy(self.c);c['A']['evidence'].append({'id':'BA2','text':'本公司直接采购铁矿石。','field':'upstream'})
        with self.assertRaisesRegex(ValueError,'shorter'):paths.derive_pair(self.a,c,self.p)

    def test_a_preselected_class_is_rejected_and_bad_pairs_stay_isolated(self):
        with self.assertRaisesRegex(ValueError,'preselected'):paths.derive_pair(self.a,self.c,{**self.p,'relation':'DISTANT'})
        answer,errors=paths.check_relations(self.a,self.c,{'id':'P1','pairs':[self.p]})
        self.assertEqual(len(answer['pairs']),1);self.assertIn('S',errors)

    def test_review_packet_has_one_citation_id_and_no_primary_path(self):
        packet=paths.teacher_packet('relations_review',[{**self.a,'primary':self.p}],self.c)
        self.assertNotIn('evidence_id',packet['articles'][0]['industry_context'][0])
        self.assertNotIn('INPUT_OUTPUT',str(packet));self.assertNotIn('DISTANT',str(packet))

    def test_class_agreement_without_same_material_path_is_not_consensus(self):
        p=paths.derive_pair(self.a,self.c,self.p);self.assertTrue(paths.pair_agreement(p,copy.deepcopy(p)))
        other=copy.deepcopy(p);other['intermediates'][0]['edge_kind']='OTHER'
        self.assertFalse(paths.pair_agreement(p,other))
        other=copy.deepcopy(p);other['intermediates'].append(copy.deepcopy(other['intermediates'][0]))
        self.assertFalse(paths.pair_agreement(p,other))

    def test_pending_transmission_and_a_specific_material_are_required(self):
        with self.assertRaisesRegex(ValueError,'pending transmission'):
            paths.derive_pair(self.a,self.c,{**self.p,'missing_axes':['REGION']})
        p=copy.deepcopy(self.p);p['intermediates'][0]['product']='材料'
        with self.assertRaisesRegex(ValueError,'Generic'):paths.derive_pair(self.a,self.c,p)

    def test_negative_context_binding_preserves_the_ai_verdict_and_its_provenance(self):
        raw={**self.p,'family':'NO_SUPPORTED_PATH','business_ids':[],'endpoint_role':'NONE',
             'endpoint_product':'','intermediates':[],'missing_axes':[],'conditions':[],
             'reason':'给定主营背景下未发现支持路径。'}
        value=paths.derive_pair(self.a,self.c,raw)
        self.assertEqual(value['relation'],'NONE');self.assertEqual(raw['business_ids'],[])
        self.assertEqual(value['business_ids'],['BA'])
        self.assertEqual(value['business_reference_mode'],'all_provided_target_context_not_AI_selected')
