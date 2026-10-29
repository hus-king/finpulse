import copy
import hashlib
import json
import unittest

from experiments.news_processing import boundary_dataset as boundary, bulk_dataset as bulk
from experiments.news_processing.contract import digest
from experiments.news_processing.silver_trial import adapt, question_schema


class BoundaryDatasetTests(unittest.TestCase):
    def setUp(self):
        self.a={'id':'V1','date':'2026-09-09','title':'铁矿石上涨','text':'铁矿石现货价格上涨3%。',
                'targets':['600031','600019'],'group_id':'g','parent_id':'p',
                'source_level':'provider_returned_text','role':'development_train'}
        self.a['packet']=bulk.packet('V1',self.a['text'])
        self.raw={'id':'V1','scope':'INDUSTRY_SIGNAL','stage':'REPORTED','time_role':'CURRENT',
                  'subject_name':'','subject_code':'','news_span':['V1-1','V1-1'],
                  'focus_quote':self.a['text'],'focus_product':'铁矿石'}
        self.a['fact']=boundary.check_fact(self.a,self.raw)
        self.bridge={'id':'I1','company_code':'600019','company_name':'钢厂','field':'products',
                     'evidence_id':'B600019','source_sha256':'a'*64,
                     'text':'本公司使用铁矿石生产钢材。','source_url':'https://example.com/report.pdf',
                     'as_of':'2025-12-31','published_date':'2026-03-31'}
        self.a['industry_context']=[self.bridge]
        self.catalog={c:{'code':c,'name':n,'aliases':[n],
                         'primary_profile_source':{'url':'https://example.com/report.pdf','sha256':'a'*64},
                         'company':{'code':c,'name':n,'products':[text]},
                         'evidence':[{'id':'B'+c,'text':text,'field':'products'}]}
                      for c,n,text in [('600031','设备企业','生产工程机械，采购钢材。'),
                                       ('600019','钢厂','使用铁矿石生产钢材。')]}
        self.bridge['text']=self.catalog['600019']['evidence'][0]['text']
        self.pair={'code':'600031','relation':'DISTANT','business_ids':['B600031'],
                   'conditions':['铁矿石变价向钢材采购价传递仍待确认'],'why':'铁矿石→钢材→设备投入成本。',
                   'endpoint_role':'INPUT_COST','endpoint_product':'钢材','missing_axes':['TRANSMISSION'],
                   'path_basis':'POTENTIAL_TRANSMISSION',
                   'intermediates':[{'product':'钢材','news_ids':[],'bridge_ids':['I1']}]}

    def test_literal_focus_cannot_be_replaced_or_removed_by_reviewer(self):
        for changed in [{'focus_quote':'铁矿石下跌。'},{'focus_product':'原油'}]:
            with self.assertRaises(ValueError):boundary.check_fact(self.a,{**self.raw,**changed})
        review={k:v for k,v in self.raw.items() if k not in {'news_span','focus_quote','focus_product'}}
        result=boundary.check_fact(self.a,{**review,'focus_complete':True,'focus_valid':True},review=True)
        self.assertEqual(result['focus_quote'],self.a['text'])
        with self.assertRaises(ValueError):boundary.check_fact(self.a,{**review,'focus_complete':False,'focus_valid':True},review=True)

    def test_predeclared_source_anchor_is_literal_and_cannot_switch_commodities(self):
        a={**self.a,'source_focus_anchor':{'quote':self.a['text'],'product':'铁矿石'}}
        self.assertEqual(boundary.teacher_packet('facts_primary',[a],{})['articles'][0]['source_focus_anchor'],a['source_focus_anchor'])
        boundary.check_fact(a,self.raw)
        bound=boundary.check_fact(a,{k:v for k,v in self.raw.items() if k not in {'focus_quote','focus_product'}})
        self.assertEqual(bound['focus_quote'],a['source_focus_anchor']['quote'])
        with self.assertRaisesRegex(ValueError,'predeclared'):
            boundary.check_fact(a,{**self.raw,'focus_product':'铁矿'})
        with self.assertRaisesRegex(ValueError,'Nonliteral'):
            boundary.teacher_packet('facts_primary',[{**a,'source_focus_anchor':{'quote':'原油上涨。','product':'原油'}}],{})

    def test_primary_and_review_share_canonical_stage_and_time_rules(self):
        for phrase in ['财报、统计、当日报价/收盘变动报道用REPORTED','某个交易日或时点的价格/指数观察用CURRENT']:
            self.assertIn(phrase,boundary.FACT_PROMPT);self.assertIn(phrase,boundary.FACT_REVIEW_PROMPT)

    def test_unknown_anchor_stays_unknown_and_optional_echo_cannot_change_focus(self):
        a={**self.a,'source_focus_anchor':{'quote':self.a['text'],'product':'铁矿石'}}
        raw={**self.raw,'scope':'UNKNOWN','focus_quote':'','focus_product':''}
        answer=boundary.check_fact(a,raw)
        self.assertEqual(answer['scope'],'UNKNOWN');self.assertEqual(answer['focus_quote'],self.a['text'])
        answer=boundary.check_fact(a,{**self.raw,'focus_quote':'报道：'+self.a['text']})
        self.assertEqual(answer['focus_quote'],self.a['text'])
        answer=boundary.check_fact(a,{**self.raw,'focus_quote':'','focus_product':''})
        self.assertEqual(answer['scope'],'INDUSTRY_SIGNAL');self.assertEqual(answer['focus_product'],'铁矿石')

    def test_anchor_clause_can_inherit_the_actor_from_adjacent_source_context(self):
        text='甲公司公告，铁矿石采购量增长3%。'
        a={**self.a,'text':text,'packet':bulk.packet('V1',text),
           'source_focus_anchor':{'quote':'铁矿石采购量增长3%。','product':'铁矿石'}}
        raw={**self.raw,'scope':'COMPANY_EVENT','subject_name':'甲公司',
             'focus_quote':'铁矿石采购量增长3%。'}
        answer=boundary.check_fact(a,raw)
        self.assertEqual(answer['subject_name'],'甲公司')
        with self.assertRaisesRegex(ValueError,'Actor is absent'):
            boundary.check_fact(a,{**raw,'subject_name':'乙公司'})

    def test_industry_background_is_context_only_not_target_business_support(self):
        c=copy.deepcopy(self.catalog);q={'id':'G600019','text':'钢铁行业使用铁矿石生产钢材。','field':'industry_background'}
        c['600019']['industry_evidence']=[q]
        a=copy.deepcopy(self.a);a['industry_context']=[{**self.bridge,'id':'I-background','evidence_id':q['id'],'text':q['text'],'field':q['field']}]
        pair=copy.deepcopy(self.pair);pair['intermediates'][0]['bridge_ids']=['I-background']
        boundary.check_pair(a,c,pair)
        bad={**pair,'code':'600019','business_ids':['G600019']}
        answer,errors=boundary.check_relations({**a,'targets':['600019']},c,{'id':'V1','pairs':[bad]})
        self.assertEqual(answer['pairs'],[]);self.assertIn('Foreign business',errors['600019'])

    def test_concrete_external_intermediate_is_required_and_literal(self):
        boundary.check_pair(self.a,self.catalog,self.pair)
        for nodes in [[],[{'product':'利润','news_ids':[],'bridge_ids':['I1']}],
                      [{'product':'铜材','news_ids':[],'bridge_ids':['I1']}],
                      [{'product':'钢材','news_ids':[],'bridge_ids':['foreign']}]]:
            with self.assertRaises(ValueError):boundary.check_pair(self.a,self.catalog,{**self.pair,'intermediates':nodes})
        with self.assertRaisesRegex(ValueError,'own external'):
            boundary.check_pair(self.a,self.catalog,{**self.pair,'code':'600019','business_ids':['B600019']})

    def test_target_endpoint_must_come_from_its_own_cited_business(self):
        with self.assertRaisesRegex(ValueError,'absent from cited target'):
            boundary.check_pair(self.a,self.catalog,{**self.pair,'endpoint_product':'飞机'})
        with self.assertRaisesRegex(ValueError,'axis'):
            boundary.check_pair(self.a,self.catalog,{**self.pair,'missing_axes':[]})

    def test_one_hop_missing_exposure_is_conditional_without_intermediates(self):
        p={**self.pair,'relation':'CONDITIONAL','path_basis':'CONDITIONAL_EXPOSURE',
           'intermediates':[],'missing_axes':['GRADE']}
        boundary.check_pair(self.a,self.catalog,p)
        with self.assertRaisesRegex(ValueError,'shortest relation'):
            boundary.check_pair(self.a,self.catalog,{**p,'intermediates':self.pair['intermediates']})

    def test_same_class_without_same_path_is_not_silver_agreement(self):
        self.assertTrue(boundary.pair_agreement(self.pair,copy.deepcopy(self.pair)))
        other=copy.deepcopy(self.pair);other['intermediates'][0]['product']='煤炭'
        self.assertFalse(boundary.pair_agreement(self.pair,other))
        other=copy.deepcopy(self.pair);other['intermediates'][0]['bridge_ids']=['I2']
        self.assertFalse(boundary.pair_agreement(self.pair,other))
        self.assertFalse(boundary.pair_agreement(self.pair,{**self.pair,'endpoint_role':'DEMAND'}))

    def test_fabricated_or_future_bridge_cannot_support_a_path(self):
        for change in [{'text':'铁矿石生产飞机。'},{'source_sha256':'b'*64},{'published_date':'2027-01-01'}]:
            with self.assertRaises(ValueError):boundary.check_pair({**self.a,'industry_context':[{**self.bridge,**change}]},self.catalog,self.pair)

    def test_unconnected_products_cannot_be_assembled_into_a_chain(self):
        a=copy.deepcopy(self.a);c=copy.deepcopy(self.catalog)
        a['industry_context'][0]['text']='钢材用于制造设备。'
        c['600019']['evidence'][0]['text']=a['industry_context'][0]['text']
        with self.assertRaisesRegex(ValueError,'adjacent products'):boundary.check_pair(a,c,self.pair)

    def test_blind_review_has_uniform_context_without_gold_path(self):
        self.a['expected']=self.pair
        packet=boundary.teacher_packet('relations_review',[self.a],self.catalog)
        self.assertEqual(packet['articles'][0]['industry_context'],[self.bridge])
        self.assertNotIn('POTENTIAL_TRANSMISSION',str(packet))
        self.assertNotIn('intermediates',str(packet))
        facts=boundary.teacher_packet('facts_primary',[self.a],self.catalog)
        self.assertNotIn('companies',facts);self.assertNotIn('industry_context',str(facts))

    def test_student_keeps_source_context_and_does_not_get_chosen_path(self):
        row=boundary.example(self.a,self.catalog,self.a['fact'],self.pair,reviewed=True)
        state=adapt(row,question_schema(expanded=True,staged=True))['state']
        self.assertEqual(state['industry_context'],[{k:self.bridge[k] for k in
            ('company_code','company_name','field','text','as_of','published_date')}])
        self.assertEqual(json.loads(row['messages'][1]['content'])['industry_context'],[self.bridge])
        self.assertNotIn('source_sha256',str(state));self.assertNotIn('source_url',str(state))
        self.assertEqual(state['event_focus']['product'],'铁矿石')
        self.assertNotIn('DISTANT',str(state));self.assertNotIn('intermediates',str(state))
        for key in ['relation','path','selected_intermediary']:
            row2=copy.deepcopy(row);inputs=json.loads(row2['messages'][1]['content']);inputs['industry_context'][0][key]='DISTANT'
            row2['messages'][1]['content']=json.dumps(inputs);row2['sha256']=digest({k:v for k,v in row2.items() if k!='sha256'})
            with self.assertRaisesRegex(ValueError,'Annotated'):adapt(row2,question_schema(expanded=True,staged=True))

    def test_a_full_fastwire_needs_raw_body_receipt_not_title_length(self):
        a={'title':'铜价涨3%。','content':'铜价涨3%。','publication_time_raw':'2026-09-09 10:00:00','source':'cls'}
        a['source_content_sha256']=hashlib.sha256(a['content'].encode()).hexdigest()
        raw=json.dumps({'data':{'fields':['datetime','title','content'],'items':[[a['publication_time_raw'],a['title'],a['content']]]}},ensure_ascii=False).encode()
        receipt={'status':'ok','request':{'api_name':'news','params':{'src':'cls'}},'raw_sha256':hashlib.sha256(raw).hexdigest()}
        self.assertEqual(boundary.fastwire_receipt(a,raw,receipt,0)['source_format'],'full_fastwire')
        for changed in [{**a,'content':'标题复制'}, {**a,'source':'sina'}]:
            with self.assertRaises(ValueError):boundary.fastwire_receipt(changed,raw,receipt,0)
        with self.assertRaises(ValueError):boundary.fastwire_receipt(a,raw,{**receipt,'request':{'api_name':'major_news'}},0)
