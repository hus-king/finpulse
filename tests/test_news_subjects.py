import hashlib
import importlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


class SubjectRegistryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('experiments.news_processing.subjects'),'Subject registry is missing')
        self.s=importlib.import_module('experiments.news_processing.subjects')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'raw').mkdir()
        raw=b'unit-test-source';(self.root/'raw'/'one.bin').write_bytes(raw)
        self.material={'id':'approval','issuer':'600276','publisher':'CNINFO','raw_file':'raw/one.bin',
            'quality':{'body':'ready'},'source':{'url':'https://static.cninfo.com.cn/one.PDF','raw_sha256':hashlib.sha256(raw).hexdigest(),'issuer_code':'600276'},
            'news':{'title':'药品获批','text':'证券代码：600276 证券简称：恒瑞医药\n江苏恒瑞医药股份有限公司\n近日，江苏恒瑞医药股份有限公司（以下简称“公司”）子公司福建盛迪医\n药有限公司收到获批通知。原研由德国贝朗医疗股份有限公司开发。','market_rows':[]}}

    def test_official_header_binds_formal_name_and_short_name_to_same_code(self):
        registry=self.s.build_registry(self.root,[self.material])
        aliases=registry['companies']['600276']['aliases']
        self.assertIn('恒瑞医药',[a['name'] for a in aliases]);self.assertIn('江苏恒瑞医药股份有限公司',[a['name'] for a in aliases])
        self.assertFalse(registry['human_verified'])

    def test_subsidiary_is_source_bound_to_article_and_not_unrelated_company(self):
        registry=self.s.build_registry(self.root,[self.material])
        relations=registry['relations']
        self.assertEqual([r['child_name'] for r in relations],['福建盛迪医药有限公司'])
        self.assertEqual(relations[0]['article_id'],'approval')
        self.assertNotIn('德国贝朗医疗股份有限公司',[r['child_name'] for r in relations])

    def test_raw_tamper_is_rejected_before_registration(self):
        (self.root/'raw'/'one.bin').write_bytes(b'modified')
        with self.assertRaises(ValueError):self.s.build_registry(self.root,[self.material])

    def test_conflicting_official_issuer_code_is_not_registered(self):
        self.material['issuer']='600031'
        registry=self.s.build_registry(self.root,[self.material])
        self.assertEqual(registry['companies'],{})

    def own_example(self,name,code):
        from experiments.news_processing import development as d
        registry=self.s.build_registry(self.root,[self.material])
        r={'id':'approval__600276','article_id':'approval','target_code':'600276',
            'source':self.material['source'],'state':{'news':self.material['news'],
            'company':{'name':'恒瑞医药','products':['药品'],'upstream':[],'downstream':[],'regions':[]}},
            'subject_bindings':self.s.bindings(registry,'600276','approval')}
        a=d.article_packet(r);c=d.company_packet(r);ids=[x['id'] for x in a['evidence']]
        f={'fact_id':'F1','scope':'COMPANY_EVENT','kind':'REGULATION','stage':'APPROVED','subject_name':name,
            'subject_code':code,'news_evidence_ids':ids,'subject_evidence_ids':ids,'description':'子公司药品获批',
            'temporal_role':'CURRENT','event_time_text':'近日','event_date':'','time_precision':'RELATIVE','time_evidence_ids':ids}
        facts=d.validate_facts(a,{'id':a['id'],'facts':[f],'extraction_complete':True})
        label={'id':r['id'],'own_event':'YES','own_fact_ids':['F1'],'links':[{'fact_id':'F1','transmission':'NONE',
               'price_role':'NOT_APPLICABLE','business_evidence_ids':[],'mechanism':'自身药品事件','missing_conditions':[]}]}
        return d,a,c,facts,label

    def test_formal_alias_passes_only_with_source_registration(self):
        d,a,c,facts,label=self.own_example('江苏恒瑞医药股份有限公司','600276')
        self.assertEqual(d.validate_pair(a,facts,c,label)['own_attribution_evidence'][0]['kind'],'exact_source_bound_alias')

    def test_wrapped_subsidiary_name_is_supported_by_contiguous_original_spans(self):
        d,a,c,facts,label=self.own_example('福建盛迪医药有限公司','')
        self.assertEqual(len(facts['facts']),1)
        result=d.validate_pair(a,facts,c,label)
        self.assertEqual(result['own_attribution_evidence'][0]['kind'],'explicit_article_subsidiary')

    def test_subsidiary_relation_cannot_be_carried_to_another_article(self):
        from experiments.news_processing.contract import digest
        d,a,c,facts,label=self.own_example('福建盛迪医药有限公司','')
        c['subject_bindings']['relations'][0]['article_id']='other'
        c['sha256']=digest({k:v for k,v in c.items() if k!='sha256'})
        with self.assertRaises(ValueError):d.validate_pair(a,facts,c,label)


if __name__=='__main__':unittest.main()
