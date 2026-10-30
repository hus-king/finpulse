import hashlib
import tempfile
import unittest
from pathlib import Path
from experiments.news_processing import subjects
from experiments.news_processing import event_attribution as a


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.body='证券代码：600276 证券简称：恒瑞医药\n江苏恒瑞医药股份有限公司\n近日，江苏恒瑞医药股份有限公司（以下简称“公司”）子公司福建盛迪医药有限公司收到药品批准通知。\n公司获得药品批准。\n公司表示，三一重工股份有限公司获得项目。\n2021年，公司曾获得药品批准。\n如果公司获得批准，将开展销售。\n公司拟回购股份。\n三一重工股份有限公司获得工程项目。\n公司向三一重工股份有限公司采购钢材。\n二、股份回购的进展情况\n2026年7月7日，公司回购股份。\n恒瑞医药科技有限公司获得项目。'
        other='证券代码：600031 证券简称：三一重工\n三一重工股份有限公司\n三一重工股份有限公司获得项目。'
        mats=[]
        for id_,code,body in [('one','600276',self.body),('other','600031',other),('third','600276',self.body.split('近日')[0]+'\n福建盛迪医药有限公司收到批准。')]:
            p=self.root/(id_+'.bin');p.write_text(body)
            mats.append({'id':id_,'issuer':code,'publisher':'CNINFO','raw_file':p.name,'quality':{'body':'ready'},
                'source':{'url':'https://static.cninfo.com.cn/'+id_+'.PDF','issuer_code':code,'raw_sha256':hashlib.sha256(p.read_bytes()).hexdigest()},
                'news':{'title':'公告','text':body}})
        self.registry=subjects.build_registry(self.root,mats)

    def classify(self,text,target='600276',article='one'):
        return a.attribute_event(self.registry,article,[text],target,publication_date='2026-10-24')

    def test_explicit_subject_and_planned_event_are_own(self):
        for text in ['公司获得药品批准。','公司拟回购股份。','二、股份回购的进展情况\n2026年7月7日，公司回购股份。']:
            self.assertEqual(self.classify(text)['status'],'OWN')

    def test_issuer_header_does_not_make_peer_event_own(self):
        result=self.classify('三一重工股份有限公司获得工程项目。')
        self.assertEqual(result['status'],'NOT_OWN');self.assertEqual(result['actor_code'],'600031')

    def test_quote_history_and_hypothesis_are_unresolved(self):
        for text in ['公司表示，三一重工股份有限公司获得项目。','2021年，公司曾获得药品批准。','如果公司获得批准，将开展销售。']:
            self.assertEqual(self.classify(text)['status'],'UNRESOLVED')

    def test_subsidiary_proof_is_local_and_preserves_child_actor(self):
        text='近日，江苏恒瑞医药股份有限公司（以下简称“公司”）子公司福建盛迪医药有限公司收到药品批准通知。'
        result=self.classify(text);self.assertEqual(result['status'],'OWN')
        self.assertEqual(result['actor_name'],'福建盛迪医药有限公司');self.assertEqual(result['basis'],'article_subsidiary')
        self.assertEqual(self.classify('福建盛迪医药有限公司收到批准。',article='third')['status'],'UNRESOLVED')

    def test_company_name_alone_and_absent_quote_cannot_prove_event(self):
        for text in ['江苏恒瑞医药股份有限公司','公司签署不存在的协议。']:
            self.assertEqual(self.classify(text)['status'],'UNRESOLVED')

    def test_trade_counterparty_does_not_inherit_issuer_identity(self):
        self.assertEqual(self.classify('公司向三一重工股份有限公司采购钢材。',target='600031')['status'],'UNRESOLVED')

    def test_public_signal_requires_literal_aggregate_evidence(self):
        public={'evidence':[{'id':'h','text':'全国流通领域重要生产资料市场价格'},{'id':'e','text':'产品名称 | 本期价格（元）\n玉米 | 2000'}]}
        self.assertEqual(a.public_signal(public,['e'])['status'],'NOT_OWN')
        self.assertEqual(a.public_signal({'evidence':[{'id':'e','text':'产品价格上涨'}]},['e'])['status'],'UNRESOLVED')
        self.assertEqual(a.public_signal({'evidence':[{'id':'h','text':'中证医药指数上涨'},{'id':'e','text':'收益率上涨10%'}]},['e'])['status'],'UNRESOLVED')

    def test_disconnected_fragments_cannot_invent_actor_predicate(self):
        result=a.attribute_event(self.registry,'one',['公司','获得药品批准。'],'600276')
        self.assertEqual(result['status'],'UNRESOLVED')

    def test_repeated_clause_requires_an_unambiguous_source_position(self):
        registry=self.registry
        from experiments.news_processing.evidence import article_packet
        registry['sources']['one']['packet']=article_packet('one',{'title':'公告','text':self.body+'\n公司拟回购股份。'})
        self.assertEqual(self.classify('公司拟回购股份。')['status'],'UNRESOLVED')

    def test_similar_company_name_cannot_use_short_name_prefix(self):
        self.assertEqual(self.classify('恒瑞医药科技有限公司获得项目。')['status'],'UNRESOLVED')

    def test_not_own_does_not_mean_unrelated(self):
        result=a.relation_route({'status':'NOT_OWN'},{'label':'DIRECT'})
        self.assertEqual(result['candidate_label'],'DIRECT');self.assertFalse(result['delete_news'])

    def test_unresolved_subject_routes_none_prediction_to_review(self):
        result=a.relation_route({'status':'UNRESOLVED'},{'label':'NONE'})
        self.assertTrue(result['review_required']);self.assertEqual(result['route'],'REVIEW');self.assertFalse(result['delete_news'])
