import importlib
import importlib.util
import unittest


class ProfileAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('experiments.news_processing.development_intake'),'Intake is missing')
        self.m=importlib.import_module('experiments.news_processing.development_intake')
        self.packet=[{'code':'601857','source_url':'https://www.petrochina.com.cn/report.pdf','raw_sha256':'sourcehash',
                      'pages':[{'page':1,'text':'A股代码601857。A股简称：中国石油。中国石油天然气股份有限公司。主营原油销售。'}]}]
        self.output={'profiles':[{'code':'601857','name':'中国石油','formal_name':'中国石油天然气股份有限公司',
            'identities':[{'role':role,'pdf_page':1,'quote':quote} for role,quote in [
                ('code','A股代码601857'),('name','A股简称：中国石油'),('formal_name','中国石油天然气股份有限公司')]],
            'facts':[{'field':'products','pdf_page':1,'quote':'主营原油销售。','use_boundary':'仅证明原油销售'}]}]}

    def test_profile_uses_literal_product_quote_and_keeps_missing_upstream_empty(self):
        profiles,pending=self.m.admit_profiles(self.packet,self.output)
        self.assertFalse(pending);self.assertEqual(profiles[0]['products'],['主营原油销售。'])
        self.assertEqual(profiles[0]['upstream'],[]);self.assertFalse(profiles[0]['human_verified'])

    def test_nonliteral_selected_profile_fact_remains_pending(self):
        self.output['profiles'][0]['facts'][0]['quote']='主要销售原油'
        profiles,pending=self.m.admit_profiles(self.packet,self.output)
        self.assertEqual(profiles,[]);self.assertTrue(pending)

    def test_identity_quote_must_bind_the_requested_company_code(self):
        self.output['profiles'][0]['identities'][0]['quote']='A股简称：中国石油'
        profiles,pending=self.m.admit_profiles(self.packet,self.output)
        self.assertEqual(profiles,[]);self.assertTrue(pending)

    def test_annual_identity_coordinates_follow_declared_page(self):
        text=self.packet[0]['pages'][0]['text']
        self.packet[0]['pages'].append({'page':4,'text':text})
        for item in self.output['profiles'][0]['identities']:item['pdf_page']=4
        profiles,pending=self.m.admit_profiles(self.packet,self.output)
        registry={'companies':{},'sources':{}}
        self.m._annual_identities(registry,profiles,self.packet)
        proof=registry['companies']['601857']['aliases'][0]['proof']
        visible=registry['sources']['profile-601857']['packet']['visible']
        self.assertGreater(proof['matched_span'][0],visible.index(text)+len(text))


if __name__=='__main__':unittest.main()
