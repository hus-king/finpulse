import unittest

from experiments.news_processing import expansion_sources as sources


class ExpansionSourceTests(unittest.TestCase):
    def test_disclosure_query_uses_issuer_org_identity_not_bare_stock_code(self):
        params = sources.query_params({'code':'600276'}, '药品')
        self.assertEqual(params['stock'], '600276,gssh0600276')
        with self.assertRaises(ValueError): sources.query_params({'code':'123456'}, '药品')

    def test_html_body_excludes_navigation_and_keeps_table_units(self):
        html = '<nav>首页 导航噪声</nav><div class="TRS_Editor"><p>单位：元/吨</p><p>钢材价格变化。</p><table><tr><th>商品</th><th>价格</th></tr><tr><td>钢材</td><td>3000</td></tr></table></div><footer>推荐链接</footer>'
        body, rows = sources.html_body(html)
        self.assertNotIn('导航噪声', body); self.assertNotIn('推荐链接', body)
        self.assertIn('单位：元/吨', body)
        self.assertEqual(rows, [['商品', '价格'], ['钢材', '3000']])

    def test_missing_article_container_is_pending_not_whole_page(self):
        with self.assertRaises(ValueError): sources.html_body('<nav>首页</nav><p>登录</p>')

    def test_merged_table_cells_require_review_instead_of_guessing_columns(self):
        html = '<div class="TRS_Editor"><table><tr><td colspan="2">合计</td></tr></table></div>'
        with self.assertRaises(ValueError): sources.html_body(html)

    def test_nbs_current_body_and_full_width_note_keep_price_rows(self):
        raw='<div class="trs_editor_view"><p>单位：元/吨</p><table><tr><td>钢材</td><td>3000</td></tr><tr><td colspan="2">注：上期为三月。</td></tr></table></div>'
        body, rows=sources.html_body(raw)
        self.assertIn('单位：元/吨',body)
        self.assertEqual(rows,[['钢材','3000'],['注：上期为三月。']])

    def test_repeated_issuer_announcements_use_conservative_family(self):
        a = {'issuer':'600031','title':'关于回购股份进展的公告','published_date':'2026-08-01'}
        b = {**a, 'title':'关于回购股份进展暨回购完成的公告','published_date':'2026-09-01'}
        self.assertEqual(sources.event_family(a), sources.event_family(b))
        self.assertNotEqual(sources.event_family(a), sources.event_family({**a, 'issuer':'600276'}))

    def test_topic_and_publisher_do_not_prove_group_independence(self):
        a = {'publisher':'NBS','title':'2026年8月份能源生产情况','published_date':'2026-09-15','url':'https://www.stats.gov.cn/a'}
        self.assertEqual(sources.event_family(a), 'macro-release-2026-09-15')

    def test_publication_date_cannot_be_inferred_as_first_seen(self):
        item = sources.source_metadata({'url':'https://www.stats.gov.cn/a','published_date':'2026-09-15'}, b'body', '2026-10-12T20:30:00+08:00')
        self.assertEqual(item['first_seen_at'], '2026-10-12T20:30:00+08:00')
        self.assertEqual(item['published_precision'], 'date_only')

    def test_resolved_source_provenance_is_kept_with_material(self):
        info={'final_url':'https://www.stats.gov.cn/resolved','status':200,'request_sha256':'request','cache_file':'raw/doc.json'}
        metadata=sources.source_metadata({'url':'https://www.stats.gov.cn/a','published_date':'2026-09-15'},b'body','2026-10-12T20:30:00+08:00',info)
        self.assertEqual(metadata['final_url'],'https://www.stats.gov.cn/resolved')
        self.assertEqual(metadata['http_status'],200)

    def test_body_duplicates_union_entire_families_and_empty_bodies_do_not_merge(self):
        items=[{'group_id':'A','body_sha256':'same'},{'group_id':'B','body_sha256':'same'},
               {'group_id':'B','body_sha256':'different'},{'group_id':'C','body_sha256':None}]
        sources.merge_body_families(items)
        self.assertEqual([x['group_id'] for x in items],['A','A','A','C'])


if __name__ == '__main__': unittest.main()
