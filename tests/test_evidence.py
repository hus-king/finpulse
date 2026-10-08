import json
import unittest
from pydantic import ValidationError
from backend import prompts

BASE = {'sentiment_score': 25, 'assessment': 'positive', 'confidence': 'medium', 'horizon': 'medium',
        'summary': '订单落地可能改善业务需求', 'causal_chain': ['公司披露订单', '有望增加收入', '执行进度待确认'],
        'uncertainty': '未披露利润率', 'positive_factors': ['新订单落地'], 'negative_factors': [], 'watch_points': ['交付与收入确认']}

class EvidenceTests(unittest.TestCase):
    def parse(self, **change):
        self.assertTrue(hasattr(prompts, 'EvidenceAnalysis'), 'Missing independent v4 analysis contract')
        return prompts.parse_json(json.dumps({**BASE, **change}), prompts.EvidenceAnalysis)

    def test_insufficient_is_completed_but_has_no_directional_score(self):
        result = self.parse(sentiment_score=None, assessment='insufficient', positive_factors=[])
        self.assertIsNone(result['sentiment_score'])
        with self.assertRaises(ValidationError):
            self.parse(sentiment_score=0, assessment='insufficient')

    def test_score_state_sign_and_granularity_must_agree(self):
        for values in ({'sentiment_score': -25}, {'sentiment_score': 26}, {'sentiment_score': True}, {'assessment': 'neutral'}, {'confidence': 'certain'}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.parse(**values)

    def test_mixed_retains_both_effects_without_forced_zero(self):
        result = self.parse(sentiment_score=None, assessment='mixed', negative_factors=['库存减值风险'])
        self.assertEqual(result['negative_factors'], ['库存减值风险'])
        with self.assertRaises(ValidationError):
            self.parse(sentiment_score=None, assessment='mixed', negative_factors=[])

    def test_overview_preserves_conflicting_directions_and_excludes_legacy_zero(self):
        from backend.evidence import build_overview
        news = [{'id': str(i), 'title': '事件'+str(i), 'analysis_status':'completed', 'analysis':{**BASE, 'version':prompts.PROMPT_VERSION, **change}} for i,change in enumerate(({}, {'assessment':'negative','sentiment_score':-30,'negative_factors':['成本压力']}, {'assessment':'insufficient','sentiment_score':None}))]
        news.append({'id':'old','analysis_status':'completed','score':0,'analysis':{'sentiment_score':0}})
        result = build_overview(news)
        self.assertEqual(result['status'], 'mixed')
        self.assertEqual(result['counts']['insufficient'], 1)
        self.assertEqual(result['counts']['legacy'], 1)
        self.assertTrue(result['opportunities'] and result['risks'])

    def test_material_event_beats_newer_price_bulletin(self):
        from backend.evidence import rank_events
        rows = [{'id':'price','title':'今日盘中突破年线个股','time':'2026-10-08','text_source':'search_fragments'}, {'id':'order','title':'公司公告签署重大订单','time':'2026-10-06','text_source':'extracted_body','date_status':'body_verified'}]
        self.assertEqual(rank_events(rows)[0]['id'], 'order')

    def test_insufficient_potential_factors_are_not_promoted_to_company_opportunities(self):
        from backend.evidence import build_overview
        row={'id':'missing','title':'行业需求变化','analysis_status':'completed','analysis':{**BASE,'version':prompts.PROMPT_VERSION,'assessment':'insufficient','sentiment_score':None}}
        overview=build_overview([row])
        self.assertEqual(overview['opportunities'],[])
        self.assertEqual(overview['status'],'insufficient')
        self.assertTrue(overview['watch_points'])

    def test_stale_analysis_is_not_part_of_current_company_direction(self):
        from backend.evidence import build_overview
        row={'id':'old','title':'上次事件','stale':True,'analysis_status':'completed','analysis':{**BASE,'version':prompts.PROMPT_VERSION}}
        overview=build_overview([row])
        self.assertEqual(overview['status'],'insufficient')
        self.assertEqual(overview['opportunities'],[])
        self.assertEqual(overview['counts']['stale'],1)
