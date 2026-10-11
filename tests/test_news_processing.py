import copy
import unittest

from experiments.news_processing.contract import (
    QUESTIONS, input_digest, validate_reference, validate_prediction,
)
from experiments.news_processing.pipeline import route, render_review


def record():
    return {
        "id": "steel__600031", "group_id": "steel", "target_code": "600031",
        "state": {"company": {"name": "三一重工", "products": ["工程机械"],
                              "upstream": ["钢材"], "downstream": [], "regions": []},
                  "news": {"title": "钢材价格上涨", "text": "统计显示普通中板价格上涨。", "market_rows": []}},
        "quality": {"body": "ready", "date": "ready", "profile": "ready"},
        "source": {"url": "https://example.org/steel", "published_date": "2026-08-01"},
    }


def reference():
    return {"own_event": "NO", "industry_relation": "DIRECT",
            "news_quote": "普通中板价格上涨", "company_quote": "钢材",
            "own_quote": "", "mechanism": "钢材价格影响工程机械的原料成本",
            "missing_conditions": []}


def prediction(own="NO", industry="CLEAR", confidence=.91):
    return {"own_event": {"label": own, "probabilities": {"YES": 1-confidence if own=="NO" else confidence,
                                                          "NO": confidence if own=="NO" else 1-confidence}},
            "industry_relation": {"label": industry, "probabilities": {
                k: confidence if k==industry else (1-confidence)/2 for k in ("CLEAR", "POSSIBLE", "NONE")}}}


class NewsContractTest(unittest.TestCase):
    def test_two_questions_have_different_attribution(self):
        self.assertEqual(set(QUESTIONS), {"own_event", "industry_relation"})
        self.assertEqual(set(QUESTIONS["own_event"]["criteria"]), {"YES", "NO"})

    def test_industry_evidence_is_not_own_company_event(self):
        self.assertEqual(validate_reference(record(), reference())["own_event"], "NO")
        bad = reference(); bad.update(own_event="YES", own_quote="三一重工")
        with self.assertRaises(ValueError): validate_reference(record(), bad)

    def test_own_event_requires_name_in_news_evidence(self):
        r = record(); r["state"]["news"]["text"] = "三一重工营业收入增长19.7%。"
        ref = reference(); ref.update(own_event="YES", own_quote="三一重工营业收入增长19.7%", news_quote="营业收入增长19.7%", industry_relation="NONE", company_quote="")
        self.assertEqual(validate_reference(r, ref)["own_event"], "YES")

    def test_own_quote_cannot_only_be_a_company_name(self):
        r = record(); r["state"]["news"]["text"] = "三一重工营业收入增长19.7%。"
        ref = reference(); ref.update(own_event="YES", own_quote="三一重工")
        with self.assertRaises(ValueError): validate_reference(r, ref)

    def test_quotes_are_contiguous_and_not_from_company_background(self):
        bad = reference(); bad["news_quote"] = "钢材"
        with self.assertRaises(ValueError): validate_reference(record(), bad)
        bad = reference(); bad["company_quote"] = "汽车"
        with self.assertRaises(ValueError): validate_reference(record(), bad)

    def test_table_data_is_kept_as_evidence(self):
        r = record(); r["state"]["news"]["market_rows"] = [["产品", "单位", "涨幅"], ["钢材", "吨", "3.0%"]]
        ref = reference(); ref["news_quote"] = "钢材 | 吨 | 3.0%"
        self.assertEqual(validate_reference(r, ref)["news_quote"], ref["news_quote"])

    def test_unknown_is_kept_for_review(self):
        ref = reference(); ref.update(own_event="UNKNOWN", industry_relation="UNKNOWN", news_quote="", company_quote="")
        self.assertEqual(validate_reference(record(), ref)["industry_relation"], "UNKNOWN")

    def test_conditional_reference_requires_conditions(self):
        ref = reference(); ref["industry_relation"] = "CONDITIONAL"
        with self.assertRaises(ValueError): validate_reference(record(), ref)
        ref["missing_conditions"] = ["待确认公司在当地的项目参与情况"]
        self.assertEqual(validate_reference(record(), ref)["industry_relation"], "CONDITIONAL")

    def test_input_hash_ignores_labels_but_tracks_profile_and_table(self):
        r = record(); digest = input_digest(r); r["expected"] = {"own_event": "YES"}
        self.assertEqual(input_digest(r), digest)
        r["state"]["company"]["products"].append("新产品")
        self.assertNotEqual(input_digest(r), digest)

    def test_invalid_and_nan_probabilities_rejected(self):
        for value in (float("nan"), -0.1, 1.1):
            p = prediction(); p["own_event"]["probabilities"]["YES"] = value
            with self.assertRaises(ValueError): validate_prediction(p)
        p = prediction(); p["own_event"]["label"] = "YES"
        with self.assertRaises(ValueError): validate_prediction(p)


class ShadowRoutingTest(unittest.TestCase):
    def test_none_never_deletes_or_hides(self):
        result = route(record(), prediction(industry="NONE"))
        self.assertTrue(result["retained"])
        self.assertTrue(result["human_review_required"])
        self.assertEqual(result["suggested_route"], "lower_priority_review")
        self.assertFalse(result["production_enabled"])

    def test_industry_and_own_go_to_different_analysis(self):
        self.assertEqual(route(record(), prediction())["suggested_route"], "industry_analysis")
        self.assertEqual(route(record(), prediction(own="YES"))["suggested_route"], "own_company_analysis")

    def test_possible_or_low_confidence_go_to_large_model_review(self):
        self.assertEqual(route(record(), prediction(industry="POSSIBLE"))["suggested_route"], "large_model_review")
        self.assertEqual(route(record(), prediction(confidence=.6))["suggested_route"], "large_model_review")

    def test_pending_material_skips_student_and_requests_supplement(self):
        r = record(); r["quality"]["body"] = "pending"
        self.assertEqual(route(r, None)["suggested_route"], "supplement_material")
        r = record(); r["quality"]["profile"] = "pending"
        self.assertEqual(route(r, prediction())["suggested_route"], "supplement_material")

    def test_missing_quality_is_pending(self):
        r = record(); del r["quality"]
        self.assertEqual(route(r, None)["suggested_route"], "supplement_material")

    def test_missing_or_invalid_prediction_kept_for_review(self):
        self.assertEqual(route(record(), None)["suggested_route"], "large_model_review")
        self.assertEqual(route(record(), {"own_event": "YES"})["suggested_route"], "large_model_review")

    def test_render_escapes_news_and_refuses_active_urls(self):
        r = record(); r["state"]["news"]["title"] = "<script>alert(1)</script>"
        r["source"]["url"] = "javascript:alert(1)"
        page = render_review([r], {}, {})
        self.assertNotIn("<script>alert", page)
        self.assertNotIn('href="javascript:', page)
        self.assertIn("&lt;script&gt;", page)


if __name__ == "__main__": unittest.main()
