import unittest
from backend.news_cleaning import material_kind

class MaterialQualityTests(unittest.TestCase):
    def test_company_mentions_cannot_promote_downloads_or_tutorials_to_news(self):
        for title in ('Ay体育2025版V51.7.22-2265安卓网','股票代码验证怎么做？沪深港美四市场规则拆解与工程实践','深度研究方法论（六维交叉验证法）'):
            with self.subTest(title=title):
                self.assertEqual(material_kind({'title':title,'url':'https://example.com/article','content':'贵州茅台600519'}),'non_news_material')

    def test_known_index_profile_and_quote_paths_are_not_articles(self):
        for url,kind in [('https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/sh600519.phtml','news_index'),('https://m.qcc.com/firm/abc.html','company_profile'),('https://www.msn.com/zh-cn/money/watchlist?id=abc','quote_page'),('https://www.fscinda.com/product/product-list/hunhe/011186/index.html','company_profile')]:
            with self.subTest(url=url):
                self.assertEqual(material_kind({'title':'贵州茅台','url':url}),kind)

    def test_actual_company_and_industry_news_remain_eligible(self):
        for title,url in [('贵州茅台发布市场化改革公告','https://finance.eastmoney.com/a/202610083889730080.html'),('公司发布安卓应用提升客户服务','https://example.com/news'),('腾讯游戏软件下载量增长，带动平台收入','https://example.com/software'),('WTI回落，炼化利润受关注','https://www.fxstreet.hk/amp/news/wti'),('股票代码变更公告','https://www.sse.com.cn/disclosure/announcement.pdf')]:
            with self.subTest(title=title):
                self.assertIsNone(material_kind({'title':title,'url':url}))

    def test_missing_publication_date_does_not_invent_historical_returns(self):
        from backend.research import forward_returns
        result=forward_returns([{'id':'legacy'}],[{'date':'2026-10-08','close':10}])
        self.assertIsNone(result['items'][0]['publication_date'])
        self.assertIsNone(result['items'][0]['base_date'])
