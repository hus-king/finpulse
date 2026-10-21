import copy
import unittest
import json
from pathlib import Path
from pydantic import ValidationError
from backend.quant.rules import RulePackage, RuleStrategy, normalize_package, EXAMPLE
from test_quant_model import history


class UserRuleTests(unittest.TestCase):
    def test_package_normalization_digest_and_executable_scoring(self):
        first,digest=normalize_package(copy.deepcopy(EXAMPLE))
        second,other=normalize_package(dict(reversed(list(EXAMPLE.items()))))
        self.assertEqual(first,second);self.assertEqual(digest,other)
        package={**first,'universe':['000001','600519'],'filters':[],
                 'score':{'op':'add','args':[{'value':1},{'op':'mul','args':[{'feature':'return_20'},{'value':2}]}]}}
        strategy=RuleStrategy(package,'user-test',1,digest)
        bars=history();result=strategy.decide({'000001':bars,'600519':bars},bars[-1]['date'])
        self.assertAlmostEqual(result['scores']['000001'],1+2*(bars[-1]['close']/bars[-21]['close']-1))
        self.assertEqual(result['target_weights'],{'000001':.2,'600519':.2})
        self.assertEqual(strategy.metadata()['origin'],'user')

    def test_filtered_out_stock_is_cash_and_zero_division_is_failure(self):
        package=copy.deepcopy(EXAMPLE);package['universe']=['000001'];package['filters']=[{'left':{'feature':'return_20'},'op':'lt','right':{'value':-1}}]
        strategy=RuleStrategy(package,'user-test',1,'a'*64);bars=history()
        self.assertEqual(strategy.decide({'000001':bars},bars[-1]['date'])['target_weights'],{})
        package['filters']=[];package['score']={'op':'div','args':[{'value':1},{'value':0}]}
        with self.assertRaises(ValueError):RuleStrategy(package,'user-test',1,'a'*64).decide({'000001':bars},bars[-1]['date'])

    def test_invalid_numbers_fields_universe_and_quota_are_rejected(self):
        for value in [True,'0.2',float('nan'),float('inf'),.21]:
            package=copy.deepcopy(EXAMPLE);package['allocation']['per_stock_weight']=value
            with self.subTest(value=value),self.assertRaises((ValidationError,ValueError)):normalize_package(package)
        for patch in [{'owner':'victim'},{'schema_version':'python'},{'universe':['000001','000001']},{'universe':['900001']},{'score':{'feature':'os.system'}},{'score':{'value':True}},{'score':{'op':'exec','args':[]}}]:
            with self.subTest(patch=patch),self.assertRaises((ValidationError,ValueError)):normalize_package({**EXAMPLE,**patch})

    def test_depth_and_total_expression_nodes_are_bounded(self):
        expr={'value':1}
        for _ in range(7):expr={'op':'abs','args':[expr]}
        with self.assertRaises((ValidationError,ValueError)):normalize_package({**EXAMPLE,'score':expr})
        expr={'op':'add','args':[{'op':'add','args':[{'value':1}]*8}]*8}
        package={**EXAMPLE,'score':expr,'filters':[{'left':expr,'op':'gt','right':{'value':0}}]*2}
        with self.assertRaises((ValidationError,ValueError)):normalize_package(package)

    def test_schema_advertises_expression_contract_and_future_bars_fail(self):
        schema=RulePackage.model_json_schema()
        self.assertIn('OperationExpr',schema['$defs'])
        bars=history();strategy=RuleStrategy(EXAMPLE,'user-test',1,'a'*64)
        with self.assertRaises(ValueError):strategy.decide({code:bars for code in EXAMPLE['universe']},'2099-01-01')

    def test_exported_contract_matches_runtime_and_allocation_obeys_total_cap(self):
        root=Path(__file__).resolve().parents[1]/'examples/strategies'
        self.assertEqual(json.loads((root/'finpulse-rules-v1.schema.json').read_text()),RulePackage.model_json_schema())
        self.assertEqual(json.loads((root/'momentum-v1.json').read_text()),EXAMPLE)
        package={**EXAMPLE,'score':{'value':1},'filters':[],
                 'selection':{'top_n':2,'min_score':1},'allocation':{'per_stock_weight':.2,'max_total_weight':.3}}
        bars=history();result=RuleStrategy(package,'user-test',1,'a'*64).decide({code:bars for code in EXAMPLE['universe']},bars[-1]['date'])
        self.assertEqual(result['target_weights'],{'000001':.15,'300750':.15})
