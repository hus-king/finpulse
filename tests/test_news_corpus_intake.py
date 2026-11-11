import json
import tempfile
import unittest
from pathlib import Path

from experiments.news_processing.corpus_intake import (
    WindowCollector, compact_article, normalize_article, site_records, unique_articles,
)


def response(rows):
    return json.dumps({'code': 0, 'data': {'fields': ['datetime', 'title', 'content'],
        'items': rows}}).encode()


class NewsCorpusIntakeTests(unittest.TestCase):
    def test_saturated_windows_split_without_losing_boundaries(self):
        calls = []
        def fetch(api, params, fields):
            calls.append(params.copy())
            if params['start_date'] == '2026-10-23 10:00:00' and params['end_date'].endswith('10'):
                return response([['2026-10-23 10:00:00', 'a', 'a'],
                                 ['2026-10-23 10:00:10', 'b', 'b']])
            return response([[params['start_date'], 'leaf', 'text']])
        with tempfile.TemporaryDirectory() as tmp:
            c = WindowCollector(Path(tmp), fetch, max_requests=5)
            result = c.collect('news', 'cls', '2026-10-23 10:00:00', '2026-10-23 10:00:10', cap=2)
            self.assertTrue(result['complete'])
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[1]['end_date'], calls[2]['start_date'])
            self.assertEqual(calls[-1]['end_date'], '2026-10-23 10:00:10')
            # Replaying cached calls consumes no further provider requests.
            c.collect('news', 'cls', '2026-10-23 10:00:00', '2026-10-23 10:00:10', cap=2)
            self.assertEqual(len(calls), 3)

    def test_budget_exhaustion_and_provider_errors_are_not_empty_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = WindowCollector(Path(tmp), lambda *a: response([['x','a','b']]*2), max_requests=1)
            result = c.collect('news', 'cls', '2026-10-23 10:00:00', '2026-10-23 11:00:00', cap=2)
            self.assertFalse(result['complete'])
            self.assertTrue(result['pending_windows'])
        with tempfile.TemporaryDirectory() as tmp:
            c = WindowCollector(Path(tmp), lambda *a: b'{"code":-1,"msg":"denied"}', max_requests=1)
            result = c.collect('major_news', '新浪财经', '2026-10-23 10:00:00', '2026-10-23 11:00:00')
            self.assertFalse(result['complete'])
            self.assertEqual(result['failed_requests'], 1)

    def test_date_body_flags_never_supply_a_relation_label(self):
        row = normalize_article({'title': '标题', 'content': '', 'time_raw': 'bad'}, {'provider':'test'})
        self.assertIn('missing_body', row['quality_flags'])
        self.assertIn('missing_or_invalid_time', row['quality_flags'])
        self.assertNotIn('relation', row)
        self.assertFalse(row['training_admitted'])
        dated = normalize_article({'title':'x','content':'有效数据 12 | 亿元',
            'time_raw':'2026-10-23T01:00:00Z'}, {'provider':'test'},
            window=['2026-10-23 10:00:00','2026-10-23 11:00:00'])
        self.assertIn('outside_requested_window', dated['quality_flags'])
        self.assertIn('12 | 亿元', dated['content'])

    def test_private_records_and_ai_scores_cannot_become_source_news(self):
        payload = {'stock':{'code':'600031'}, 'news':[{'title':'新闻','content':'正文',
            'time':'2026-10-23','analysis':{'sentiment_score':80}, 'score':80}],
            'sentiment':{'posts':[{'content':'private community'}]}}
        row = {'namespace':'dashboard','record_key':'600031','owner':'','payload':json.dumps(payload)}
        output = list(site_records(row))
        articles = [r for r in output if r['kind']=='article']
        judgments = [r for r in output if r['kind']=='legacy_ai_judgment']
        self.assertEqual(len(articles),1)
        self.assertEqual(len(judgments),1)
        self.assertNotIn('analysis', articles[0]['record'])
        self.assertNotIn('score', articles[0]['record'])
        self.assertNotIn('private community', json.dumps(output))
        self.assertEqual(list(site_records({**row,'owner':'user-1'})),[])
        self.assertEqual(list(site_records({**row,'namespace':'job'})),[])

    def test_filtered_and_raw_site_news_are_both_preserved(self):
        p = {'code':'600031','raw':{'searches':[{'provider':'search',
            'response':{'results':[{'title':'raw','content':'搜索片段'}]}}]},
            'cleaning':{'items':[], 'audit':[{'title':'filtered','cleaned_text':'有效正文',
                'status':'excluded','reason_codes':['not_primary_entity']}]}}
        out = list(site_records({'namespace':'collection','record_key':'k','owner':'','payload':json.dumps(p)}))
        articles = [r for r in out if r['kind']=='article']
        self.assertEqual({r['record']['title'] for r in articles},{'raw','filtered'})
        self.assertTrue(all('relation' not in r['record'] for r in articles))

    def test_search_relevance_is_not_an_ai_label_and_profile_text_is_retained(self):
        p={'raw':{'searches':[{'response':{'results':[{'title':'新闻','content':'片段','score':.93}]}}]}}
        row={'namespace':'collection','record_key':'k','owner':'','payload':json.dumps(p)}
        self.assertFalse(any(r['kind']=='legacy_ai_judgment' for r in site_records(row)))
        profile={'data':{'code':'600031','main_business':'混凝土机械','note':'未知收入占比'}}
        row.update(namespace='company_business',payload=json.dumps(profile))
        self.assertEqual(list(site_records(row))[0]['profile']['main_business'],'混凝土机械')

    def test_html_cleanup_retains_table_values_and_per_observation_dates(self):
        row=normalize_article({'title':'价格','content':'<nav>推荐</nav><table><tr><td>铜</td><td>80000元/吨</td></tr></table>',
            'time_raw':'2026-10-23','url':'https://example.org/a'}, {'provider':'test'})
        self.assertNotIn('推荐',row['content'])
        self.assertIn('铜 | 80000元/吨',row['content'])
        self.assertEqual(row['observations'][0]['publication_time_raw'],'2026-10-23')

    def test_exact_versions_keep_provenance_and_do_not_claim_event_groups(self):
        a = normalize_article({'title':'一条新闻','content':'产量增长20%','time_raw':'2026-10-23'}, {'provider':'one'})
        b = normalize_article({'title':'一条新闻','content':'产量增长20%','time_raw':'2026-10-23'}, {'provider':'two'})
        edited = normalize_article({'title':'一条新闻','content':'产量增长30%','time_raw':'2026-10-23'}, {'provider':'one'})
        merged = unique_articles([a,b,edited])
        self.assertEqual(len(merged),2)
        self.assertEqual(len(merged[0]['observations']),2)
        self.assertTrue(all(r['split']=='unassigned' for r in merged))
        self.assertTrue(all('event_group' not in r for r in merged))

    def test_server_compaction_keeps_every_observation_without_repeating_bodies(self):
        seen=set()
        first={'kind':'article','origin':{'record_key':'one'},'record':{'title':'标题','content':'完整正文','time_raw':'2026-10-23'}}
        second={'kind':'article','origin':{'record_key':'two'},'record':{'title':'标题','content':'完整正文','time_raw':'2026-10-24'}}
        a,b=compact_article(first,seen),compact_article(second,seen)
        self.assertEqual(a['kind'],'article')
        self.assertEqual(b['kind'],'article_observation')
        self.assertEqual(a['article_source_id'],b['article_source_id'])
        self.assertNotIn('content',b['record'])
        self.assertEqual(b['record']['time_raw'],'2026-10-24')
        self.assertEqual(b['origin']['record_key'],'two')

    def test_identical_text_on_different_dates_is_not_one_dated_article(self):
        a=normalize_article({'title':'收盘','content':'指数上涨1%','time_raw':'2026-10-23'},{'provider':'one'})
        b=normalize_article({'title':'收盘','content':'指数上涨1%','time_raw':'2026-10-24'},{'provider':'one'})
        self.assertEqual(len(unique_articles([a,b])),2)
        self.assertEqual(a['content_group_id'],b['content_group_id'])

    def test_missing_required_provider_fields_is_failure(self):
        raw=json.dumps({'code':0,'data':{'fields':['datetime','title'],
            'items':[['2026-10-23 10:01:00','only title']]}}).encode()
        with tempfile.TemporaryDirectory() as tmp:
            c=WindowCollector(Path(tmp),lambda *a:raw,max_requests=1)
            result=c.collect('news','cls','2026-10-23 10:00:00','2026-10-23 11:00:00')
            self.assertFalse(result['complete'])
            self.assertEqual(result['failed_requests'],1)

    def test_valid_date_fallback_keeps_original_conflicting_metadata(self):
        payload={'stock':{'code':'600031'},'news':[{'title':'标题','content':'正文',
            'published_date':'bad','time':'2026-10-23'}]}
        out=list(site_records({'namespace':'dashboard','record_key':'k','owner':'','payload':json.dumps(payload)}))
        self.assertEqual(out[0]['record']['time_raw'],'2026-10-23')
        self.assertEqual(out[0]['record']['date_metadata']['published_date'],'bad')

    def test_future_publication_metadata_is_retained_but_flagged(self):
        a=normalize_article({'title':'标题','content':'原始正文','time_raw':'2045-07-26'},
            {'provider':'site','collected_at':'2026-10-26T12:00:00+08:00'})
        self.assertIn('future_publication_date',a['quality_flags'])
        self.assertEqual(a['publication_time_raw'],'2045-07-26')
        self.assertFalse(a['training_admitted'])


if __name__ == '__main__':
    unittest.main()
