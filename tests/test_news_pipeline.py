import unittest
from datetime import date

from backend.market_data import build_snapshots
from backend.news_cleaning import body_publication_date, clean_report, clean_text, metadata_date, same_event


class NewsPipelineTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
