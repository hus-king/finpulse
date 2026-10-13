import unittest
import tempfile
import json
import hashlib
from pathlib import Path

from experiments.news_processing import financial, issuer, comparison
from experiments.news_processing.benchmark import checked_freeze
from experiments.news_processing.contract import digest


def filing():
    return {"id": "filing__002714", "target_code": "002714",
            "state": {"company": {"name": "牧原股份", "products": ["生猪"]},
                      "news": {"title": "2026年第一季度报告",
                               "text": "证券代码：002714 证券简称：牧原股份\n牧原食品股份有限公司2026年第一季度报告\n本公司及董事会保证本报告内容真实。",
                               "market_rows": []}},
            "source": {"url": "https://static.cninfo.com.cn/finalpage/2026-04-22/1225136604.PDF",
                       "raw_sha256": "a" * 64},
            "quality": {"body": "ready", "date": "ready", "profile": "ready"}}


SOURCES = {filing()['source']['url']:{'raw_sha256':'a'*64, 'issuer_code':'002714'}}
ACTORS = {x:{'role':'source','identity':'source:'+x,'generation_id':'call:'+x}
          for x in ['blind-a','blind-b']}
ACTORS.update({x:{'role':'candidate','identity':'candidate:'+x,'generation_id':'call:'+x}
               for x in ['rule','missing','student']})


def attributed(row):
    registry={url:{**info,'news_sha256':digest(row['state']['news'])} for url,info in SOURCES.items()}
    return issuer.attribute(row, registry)


def summarized(records, refs, candidates):
    return comparison.summarize(records, refs, candidates, actor_registry=ACTORS)


class IssuerBaselineTests(unittest.TestCase):
    def test_verified_header_report_is_own_event(self):
        result = attributed(filing())
        self.assertEqual(result["own_event"], "YES")
        self.assertIn("002714", result["evidence"])
        self.assertIsNone(result["industry_relation"])
        self.assertTrue(result["retained"])

    def test_wrong_issuer_is_no_without_industry_inference(self):
        row = filing(); row["target_code"] = "600276"
        row["state"]["company"]["name"] = "恒瑞医药"
        result = attributed(row)
        self.assertEqual(result["own_event"], "NO")
        self.assertIsNone(result["industry_relation"])

    def test_counterparty_or_subsidiary_mention_requires_review(self):
        row = filing(); row["target_code"] = "600276"
        row["state"]["company"]["name"] = "恒瑞医药"
        row["state"]["news"]["text"] += "\n恒瑞医药与本公司签订重大合同。"
        self.assertEqual(attributed(row)["own_event"], "ABSTAIN")

    def test_title_or_incidental_name_alone_does_not_prove_own_event(self):
        row = filing(); row["state"]["news"]["text"] = "证券代码：002714 证券简称：牧原股份\n市场评论认为牧原股份值得关注。"
        self.assertEqual(attributed(row)["own_event"], "ABSTAIN")

    def test_multiple_header_issuers_are_ambiguous(self):
        row = filing(); row["state"]["news"]["text"] += "\n证券代码：600276 证券简称：恒瑞医药"
        self.assertEqual(attributed(row)["own_event"], "ABSTAIN")

    def test_code_substring_and_bond_code_are_not_stock_issuer(self):
        for prefix in ("证券代码：0027149", "债券代码：002714", "证券代码：002714A"):
            row = filing(); row["state"]["news"]["text"] = prefix + "\n2026年第一季度报告"
            self.assertEqual(attributed(row)["own_event"], "ABSTAIN")

    def test_nonprimary_or_pending_sources_are_not_decided(self):
        for url in ("https://static.cninfo.com.cn.evil.org/x.PDF", "https://example.org/x.PDF"):
            row = filing(); row["source"]["url"] = url
            self.assertEqual(attributed(row)["own_event"], "ABSTAIN")
        row = filing(); row["quality"]["body"] = "pending"
        self.assertEqual(attributed(row)["own_event"], "ABSTAIN")

    def test_reference_and_expected_labels_do_not_affect_rule(self):
        row = filing(); expected = attributed(row)
        row.update(expected={"own_event": "NO"}, reference={"own_event": "NO"})
        self.assertEqual(attributed(row), expected)

    def test_drug_approval_requires_issuer_and_substantive_body(self):
        row = filing(); row['target_code'] = '600276'; row['state']['company']['name'] = '恒瑞医药'
        row['state']['news'].update(title='关于获得药品注册批准的公告',
                                  text='证券代码：600276 证券简称：恒瑞医药\n本公司近日收到国家药品监督管理局核准签发的药品注册证书。')
        self.assertEqual(issuer.attribute(row, {row['source']['url']:{'raw_sha256':'a'*64,'issuer_code':'600276','news_sha256':digest(row['state']['news'])}})['own_event'], 'YES')

    def test_guarantee_for_subsidiary_is_an_issuer_event(self):
        row = filing(); row['state']['news'].update(title='关于公司及控股子公司担保进展的公告',
                    text='证券代码：002714 证券简称：牧原股份\n本公司为控股子公司提供担保，本次担保金额为人民币一亿元。')
        self.assertEqual(attributed(row)['own_event'], 'YES')

    def test_valid_looking_source_without_verified_registry_abstains(self):
        self.assertEqual(issuer.attribute(filing())['own_event'],'ABSTAIN')
        row=filing();row['source']['raw_sha256']='b'*64
        self.assertEqual(attributed(row)['own_event'],'ABSTAIN')

    def test_quoted_other_issuer_header_cannot_override_verified_source_issuer(self):
        row=filing()
        registry={row['source']['url']:{'raw_sha256':'a'*64,'issuer_code':'600031'}}
        self.assertEqual(issuer.attribute(row,registry)['own_event'],'ABSTAIN')

    def test_news_excerpt_must_match_frozen_source_receipt(self):
        row=filing()
        registry={row['source']['url']:{'raw_sha256':'a'*64,'issuer_code':'002714','news_sha256':'bad'}}
        self.assertEqual(issuer.attribute(row,registry)['own_event'],'ABSTAIN')

    def test_a_share_header_followed_by_a_share_shortname_is_not_an_extra_code_digit(self):
        row=filing();row['state']['news']['text']=row['state']['news']['text'].replace('证券代码：002714 证券简称：','A股证券代码：002714 A股证券简称：')
        self.assertEqual(attributed(row)['own_event'],'YES')


class FinancialReconstructionTests(unittest.TestCase):
    def test_join_metric_fragments_before_taking_row_limit(self):
        rows = [["", "", "", "本报告期", "上年同期", "", "本报告期比上年同期增减", ""],
                [None, None, None, None, None, None, "（%）", None],
                ["", "营业收入（元）", "", "20", "30", "-33%", None, None],
                ["", "归属于上市公司股东的净利", "", "-10", "40", "-125%", None, None],
                [None, "润（元）", None, None, None, None, None, None]]
        out = financial.reconstruct([rows], schema="spanned-eight", units="单位：元", limit=2)
        self.assertEqual(out[0], ["项目", "本报告期", "上年同期", "本报告期比上年同期增减（%）"])
        self.assertEqual(out[-1], ["归属于上市公司股东的净利润（元）", "-10", "40", "-125%"])

    def test_cross_page_per_share_label_keeps_explicit_unit(self):
        a = [["项目", "本报告期(1)", "上年同期", "本报告期比上年同期增减变动幅度"],
             ["营业收入", "22,133", "33,402", "-33.7%"],
             ["归属于母公司股东的基本加权", "2.08", "1.89", "10.1%"]]
        b = [["平均每股收益（元）", "", "", ""]]
        out = financial.reconstruct([a, b], schema="four", units="单位：人民币百万元", limit=2)
        self.assertEqual(out[-1][0], "归属于母公司股东的基本加权平均每股收益（元）")
        self.assertEqual(out[-1][1:], ["2.08", "1.89", "10.1%"])

    def test_missing_continuation_stays_pending(self):
        rows = [["项目", "本报告期", "上年同期", "本报告期比上年同期增减"],
                ["归属于母公司股东的基本加权", "2.08", "1.89", "10.1%"]]
        with self.assertRaises(ValueError): financial.reconstruct([rows], schema="four", units="单位：元", limit=1)

    def test_unmapped_nonempty_cell_is_not_discarded(self):
        rows = [["", "", "", "本报告期", "上年同期", "", "本报告期比上年同期增减", ""],
                [None, None, None, None, None, None, "（%）", None],
                ["", "营业收入（元）", "", "20", "30", "-33%", "UNEXPECTED", None]]
        with self.assertRaises(ValueError): financial.reconstruct([rows], schema="spanned-eight", units="单位：元", limit=1)

    def test_unknown_schema_and_missing_requested_metrics_fail(self):
        with self.assertRaises(ValueError): financial.reconstruct([], schema="guess", units="单位：元", limit=1)
        rows = [["项目", "本报告期", "上年同期", "本报告期比上年同期增减"], ["营业收入", "20", "30", "-33%"]]
        with self.assertRaises(ValueError): financial.reconstruct([rows], schema="four", units="单位：元", limit=5)

    def test_following_balance_sheet_header_does_not_corrupt_complete_excerpt(self):
        rows = [["", "", "", "本报告期", "上年同期", "", "本报告期比上年同期增减", ""],
                [None, None, None, None, None, None, "（%）", None],
                ["", "营业收入（元）", "", "20", "30", "-33%", None, None],
                ["", "", "", "本报告期末", "上年度末", "", "本报告期末比上年度末增减", ""]]
        out = financial.reconstruct([rows], schema="spanned-eight", units="单位：元", limit=1)
        self.assertEqual(out[-1], ["营业收入（元）", "20", "30", "-33%"])


class ComparisonTests(unittest.TestCase):
    def fixture(self):
        from experiments.news_processing.contract import input_digest
        row = filing(); row.update(group_id='event-1',cohort='new_historical_source')
        ref = {'id':row['id'], 'input_sha256':input_digest(row), 'human_verified':False,
               'actor_ids':['blind-a','blind-b'], 'expected':{'own_event':'YES','industry_relation':'POSSIBLE'}}
        pred = {'id':row['id'], 'input_sha256':input_digest(row), 'actor_id':'rule',
                'labels':{'own_event':'ABSTAIN','industry_relation':None}}
        return row,ref,pred

    def test_abstention_and_missing_predictions_do_not_inflate_recall(self):
        row,ref,pred=self.fixture()
        stats=summarized([row],[ref],{'rule':[pred], 'missing':[]})
        for name in ['rule','missing']:
            own=stats['models'][name]['own_event']
            self.assertEqual(own['positive_support'],1)
            self.assertEqual(own['positive_identified'],0)
            self.assertEqual(own['decided'],0)
        self.assertEqual(stats['retained'],1)
        self.assertEqual(stats['deleted'],0)
        self.assertEqual(stats['models']['rule']['own_event']['abstentions'],1)
        self.assertEqual(stats['models']['missing']['own_event']['missing'],1)

    def test_candidate_actor_cannot_grade_its_own_reference(self):
        row,ref,pred=self.fixture(); pred['actor_id']='blind-a'
        with self.assertRaises(ValueError): summarized([row],[ref],{'rule':[pred]})

    def test_changed_input_and_duplicate_ids_are_rejected(self):
        row,ref,pred=self.fixture(); pred['input_sha256']='bad'
        with self.assertRaises(ValueError): summarized([row],[ref],{'rule':[pred]})
        row,ref,pred=self.fixture()
        with self.assertRaises(ValueError): summarized([row],[ref],{'rule':[pred,pred]})

    def test_conditional_correct_and_false_clear_are_counted_separately(self):
        row,ref,pred=self.fixture(); pred['labels']={'own_event':'YES','industry_relation':'POSSIBLE'}
        result=summarized([row],[ref],{'student':[pred]})['models']['student']
        self.assertEqual(result['own_event']['positive_identified'],1)
        self.assertEqual(result['industry_relation']['possible_identified'],1)
        self.assertEqual(result['industry_relation']['none_promoted_clear'],0)

    def test_source_identity_alias_cannot_be_a_candidate(self):
        row,ref,pred=self.fixture()
        actors={**ACTORS,'rule':{**ACTORS['rule'],'identity':ACTORS['blind-a']['identity']}}
        with self.assertRaises(ValueError): comparison.summarize([row],[ref],{'rule':[pred]},actor_registry=actors)

    def test_reference_mutation_is_detected_before_inference(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for name in ['inputs.jsonl','source_references.jsonl']: (root/name).write_text('original')
            freeze={'independent_test':False,'production_enabled':False,'sha256':{
                name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                for name in ['inputs.jsonl','source_references.jsonl']}}
            (root/'comparison_freeze.json').write_text(json.dumps(freeze))
            self.assertEqual(checked_freeze(root),freeze)
            (root/'source_references.jsonl').write_text('changed after predictions')
            with self.assertRaises(ValueError): checked_freeze(root)


if __name__ == "__main__":
    unittest.main()
