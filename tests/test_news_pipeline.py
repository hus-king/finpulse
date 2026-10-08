import unittest
from datetime import date

from backend.market_data import build_snapshots
from backend.news_cleaning import body_publication_date, clean_report, clean_text, metadata_date, normalize_url, same_event


class NewsPipelineTests(unittest.TestCase):
    def test_tracking_and_promotional_url_parameters_are_removed(self):
        self.assertEqual(normalize_url('https://finance.sina.cn/a.html?oid=spam&vt=4&id=123'), 'https://finance.sina.cn/a.html?id=123')

    def test_wrong_sidebar_extraction_is_not_used_as_news_body(self):
        title = '贵州茅台旗下自营渠道暂停营业一天'
        url = 'https://example.com/a'
        raw = {'date_range': ['2026-09-01', '2026-10-03'], 'searches': [{'stock': '贵州茅台', 'response': {'results': [{'title': title, 'url': url, 'published_date': '2026-09-30', 'content': '贵州茅台自营渠道计划暂停营业一天，具体原因与实际业务影响尚需核实，请查看公告原文。'}]}}]}
        result = clean_report(raw, {url: title + '\n2026-09-30\n段永平买入贵州茅台，个股当日价格上涨，相关新闻来自其他报道。'})
        self.assertEqual(result['items'][0]['text_source'], 'search_fragments')
        self.assertNotIn('段永平', result['items'][0]['cleaned_text'])

    def test_headline_and_unrelated_sidebar_without_event_evidence_are_quarantined(self):
        raw = {'date_range': ['2026-09-01', '2026-10-03'], 'searches': [{'stock': '贵州茅台', 'response': {'results': [{'title': '贵州茅台旗下自营渠道暂停营业一天', 'url': 'https://example.com/a', 'published_date': '2026-09-30', 'content': '贵州茅台旗下自营渠道暂停营业一天\n段永平买入贵州茅台，个股当日价格上涨，相关新闻来自其他报道。'}]}}]}
        result = clean_report(raw)
        self.assertEqual(result['items'], [])
        self.assertIn('insufficient_event_evidence', result['audit'][0]['reason_codes'])

    def test_completed_trade_cannot_precede_article_publication_in_metadata(self):
        raw = {'date_range': ['2026-09-01', '2026-10-03'], 'searches': [{'stock': '贵州茅台', 'response': {'results': [{'title': '段永平加仓贵州茅台', 'url': 'https://example.com/a', 'published_date': '2026-09-25', 'content': '9月28日，投资者已买入贵州茅台三万股，并发布交易截图，当日收盘价格出现变化。'}]}}]}
        result = clean_report(raw)
        self.assertEqual(result['items'], [])
        self.assertIn('event_after_publication', result['audit'][0]['reason_codes'])

    def test_body_year_is_not_mistaken_for_publication_date(self):
        self.assertIsNone(body_publication_date("2025年公司营收增长。2026年展望仍有不确定性。"))
        self.assertEqual(body_publication_date("来源：界面新闻2025-09-02 09:42\n正文"), date(2025, 9, 2))

    def test_metadata_near_midnight_converts_to_shanghai(self):
        self.assertEqual(metadata_date("Wed, 30 Sep 2026 20:10:28 GMT"), date(2026, 10, 1))

    def test_related_news_footer_does_not_enter_body(self):
        text, _ = clean_text("新闻标题足够长\n财闻 2026-09-28 13:39\n主体新闻。\n实时资讯\n其他公司持股计划公告", "新闻标题足够长")
        self.assertIn("主体新闻", text)
        self.assertNotIn("其他公司", text)

    def test_similar_numbered_plans_remain_separate(self):
        event = {"stock": "宁德时代", "category": "announcement", "effective_date": "2026-09-30", "url": "https://example.com/a"}
        left = {**event, "title": "宁德时代拟推2026年第一期员工持股计划"}
        right = {**event, "title": "宁德时代拟推2026年第二期员工持股计划", "url": "https://example.com/b"}
        self.assertFalse(same_event(left, right)[0])

    def test_different_dates_do_not_merge_recurring_events(self):
        left = {"stock": "贵州茅台", "category": "company_news", "effective_date": "2026-09-01", "url": "https://example.com/a", "title": "段永平买入3万股贵州茅台"}
        right = {**left, "effective_date": "2026-09-28", "url": "https://example.com/b"}
        self.assertFalse(same_event(left, right)[0])

    def test_old_article_is_quarantined_without_mutating_input(self):
        url = "https://example.com/a"
        raw = {"date_range": ["2026-09-01", "2026-10-01"], "searches": [{"stock": "贵州茅台", "response": {"results": [{"title": "贵州茅台股东增持计划", "url": url, "published_date": "Tue, 01 Sep 2026 00:00:00 GMT"}]}}], "extract": {"response": {"results": [{"url": url, "raw_content": "贵州茅台股东增持计划\n来源：界面新闻2025-09-02 09:42\n正文"}]}}}
        external = {}
        result = clean_report(raw, extracts=external)
        self.assertEqual(external, {})
        self.assertEqual(result["summary"]["retained"], 0)
        self.assertIn("out_of_range", result["audit"][0]["reason_codes"])

    def test_historical_price_stays_historical_and_uses_previous_close(self):
        report = {"quotes": [{"stock": "贵州茅台", "status": "error"}], "histories": [{"stock": "贵州茅台", "rows": [{"date": "2026-09-30T00:00:00", "open": 105, "close": 110}, {"date": "2026-09-29T00:00:00", "close": 100}, {"date": "2026-10-01T00:00:00", "close": None}]}]}
        result = build_snapshots(report)[0]
        self.assertFalse(result["is_realtime"])
        self.assertEqual(result["as_of_date"], "2026-09-30")
        self.assertEqual(result["change_percent"], 10)
        self.assertEqual(result["quote_status"], "error")

    def test_no_market_data_never_becomes_zero_price(self):
        snapshot = build_snapshots({})[0]
        self.assertIsNone(snapshot["close"])
        self.assertEqual(snapshot["status"], "unavailable")

    def test_body_header_date_takes_precedence_over_crawler_metadata(self):
        url = "https://example.com/xugong-capital"
        title = "股票行情快报：徐工机械（000425）9月30日主力资金净买入1.31亿元_主力研究"
        # Crawler metadata is 2026-10-08, but body header is 2026-09-30
        raw = {
            "date_range": ["2026-09-25", "2026-10-08"],
            "searches": [{
                "stock": "徐工机械",
                "response": {
                    "results": [{
                        "title": title,
                        "url": url,
                        "published_date": "2026-10-08",
                        "content": "发布时间：2026-09-30 15:30:00\n徐工机械主力资金今日呈现净买入格局，资金流动对个股形成持续支撑。"
                    }]
                }
            }]
        }
        result = clean_report(raw)
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["effective_date"], "2026-09-30")
        self.assertEqual(item["date_status"], "body_verified")
        self.assertNotIn("date_conflict", item.get("reason_codes", []))

    def test_relaxed_entity_matching_retains_brand_root_and_body_references(self):
        # 1. Title contains brand root '徐工' instead of full '徐工机械'
        url_car = "https://example.com/crane"
        title_car = "徐工汽车起重机新车申报：国六柴油动力 能抢占工程车市场吗？ - 搜狐时间线"
        # 2. Unrelated news without any entity references
        url_unrelated = "https://example.com/unrelated"
        title_unrelated = "某公司招聘新员工"
        raw = {
            "date_range": ["2026-09-25", "2026-10-08"],
            "searches": [{
                "stock": "徐工机械",
                "response": {
                    "results": [
                        {
                            "title": title_car,
                            "url": url_car,
                            "published_date": "2026-09-30",
                            "content": "徐工汽车起重机新款车型完成工信部新车申报，搭载国六柴油发动机，引发行业关注。"
                        },
                        {
                            "title": title_unrelated,
                            "url": url_unrelated,
                            "published_date": "2026-09-30",
                            "content": "某公司发布秋季招聘启事，涉及销售及行政多岗位。"
                        }
                    ]
                }
            }]
        }
        result = clean_report(raw)
        retained_titles = [item["title"] for item in result["items"]]
        self.assertIn(title_car, retained_titles)
        self.assertNotIn(title_unrelated, retained_titles)
        # Verify unrelated was filtered due to entity mismatch
        unrelated_audit = next(r for r in result["audit"] if r["url"] == url_unrelated)
        self.assertIn("not_primary_entity", unrelated_audit["reason_codes"])

    def test_traditional_and_simplified_titles_with_media_suffixes_are_merged(self):
        left = {
            "stock": "贵州茅台",
            "category": "company_news",
            "effective_date": "2026-10-06",
            "url": "https://www.moomoo.com/hant/news/post/1000627402",
            "title": "貴州茅台：今日暫停！"
        }
        right = {
            "stock": "贵州茅台",
            "category": "company_news",
            "effective_date": "2026-10-05",
            "url": "https://wap.eastmoney.com/a/202610063888722080.html",
            "title": "贵州茅台：今日暂停！ _ 东方财富网"
        }
        matched, metrics = same_event(left, right)
        self.assertTrue(matched)
        self.assertEqual(metrics["title_jaccard"], 1.0)
        self.assertEqual(metrics["title_sequence"], 1.0)

    def test_wap_and_pc_url_canonicalization_matches_same_document(self):
        u1 = "https://wap.eastmoney.com/a/202610063888722080.html?spm=123"
        u2 = "http://finance.eastmoney.com/a/202610063888722080.html"
        self.assertEqual(normalize_url(u1), normalize_url(u2))

    def test_quality_inheritance_upgrades_search_fragment_to_extracted_body(self):
        url1 = "https://www.moomoo.com/hant/news/post/1000627402"
        url2 = "https://wap.eastmoney.com/a/202610063888722080.html"
        raw = {
            "date_range": ["2026-10-01", "2026-10-08"],
            "searches": [
                {
                    "stock": "贵州茅台",
                    "response": {
                        "results": [
                            {
                                "title": "貴州茅台：今日暫停！",
                                "url": url1,
                                "published_date": "2026-10-06",
                                "content": "近日，貴州茅台旗下官微發佈公告，計劃於2026年10月6日全天對i茅台APP進行維護升級。"
                            },
                            {
                                "title": "贵州茅台：今日暂停！ _ 东方财富网",
                                "url": url2,
                                "published_date": "2026-10-05",
                                "content": "贵州茅台今日暂停摘要"
                            }
                        ]
                    }
                }
            ],
            "extract": {
                "response": {
                    "results": [
                        {
                            "url": url2,
                            "raw_content": "发布时间：2026-10-06 09:00:00\n贵州茅台：今日暂停！\n近日贵州茅台旗下官微发布公告，为持续优化i茅台APP体验，计划于2026年10月6日全天维护升级。在此期间APP将暂停使用。"
                        }
                    ]
                }
            }
        }
        result = clean_report(raw)
        # Should merge into 1 retained article
        self.assertEqual(result["summary"]["retained"], 1)
        self.assertEqual(result["summary"]["merged"], 1)
        item = result["items"][0]
        # Quality inheritance: extracted body should replace fragment
        self.assertEqual(item["text_source"], "extracted_body")
        self.assertEqual(len(item["sources"]), 2)
        self.assertEqual(item["effective_date"], "2026-10-06")
        self.assertEqual(item["date_status"], "body_verified")


if __name__ == "__main__":
    unittest.main()


