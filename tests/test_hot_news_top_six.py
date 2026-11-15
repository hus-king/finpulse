from copy import deepcopy
from types import SimpleNamespace
import unittest
from fastapi import HTTPException
from backend.hot_news_board import public_board
from backend.hot_stock_impact import HotStockImpactService


def fixture():
    rows = []
    for index in range(8):
        title = f'测试机构宣布新政策，涉及指定商品{index}'
        rows.append({'id': 'news'+str(index), 'title': title, 'summary': title, '_content': title+'原始正文',
            'rank_score': 1, 'rank': 1,
            'newsworthiness': {'total_score': 80-index, 'decision': 'hot'},
            'screening': {'decision': 'keep', 'channels': ['regulation'], 'event': {
                'novelty': 'new_development', 'importance': 'major_public', 'actor': '测试机构', 'new_fact': title}},
            'sources': [{'id': 'weibo' if index >= 6 else 'baidu', 'rank': index+1,
                         'observed_timestamp': 1000+index, 'url': 'https://example.test/news'}]})
    return {'items': rows}


class PublicTopSixTests(unittest.TestCase):
    def test_global_score_selection_is_limited_even_for_old_large_limit(self):
        feed = fixture(); original = deepcopy(feed)
        result = public_board(feed, limit=100)
        self.assertEqual([row['id'] for row in result['items']], ['news'+str(i) for i in range(6)])
        self.assertEqual(result['publication_limit'], 6)
        self.assertEqual(result['matched_total'], 6)
        self.assertEqual(result['candidate_total'], 6)
        self.assertTrue(all('_content' not in row for row in result['items']))
        self.assertEqual(feed, original)

    def test_filters_cannot_resurrect_a_lower_scoring_candidate(self):
        self.assertEqual(public_board(fixture(), source='weibo')['items'], [])
        self.assertEqual(public_board(fixture(), q='商品7')['items'], [])
        result = public_board(fixture(), sort='latest', limit=100)
        self.assertEqual({row['id'] for row in result['items']}, {'news'+str(i) for i in range(6)})
        self.assertEqual(result['items'][0]['id'], 'news5')

    def test_ties_are_stable_and_latest_time_breaks_equal_scores(self):
        feed = fixture()
        for row in feed['items']: row['newsworthiness']['total_score'] = 70
        result = public_board(feed)
        self.assertEqual([row['id'] for row in result['items']], ['news'+str(i) for i in range(7, 1, -1)])

    def test_small_valid_feed_is_not_padded(self):
        feed = fixture(); feed['items'] = feed['items'][:3]
        feed['items'][0]['screening']['decision'] = 'exclude'
        self.assertEqual(len(public_board(feed)['items']), 2)

    def test_private_analysis_cannot_access_unpublished_seventh_news(self):
        board = SimpleNamespace(feed=fixture)
        research = SimpleNamespace(store=SimpleNamespace())
        service = HotStockImpactService(research, board)
        self.assertEqual(service.topic('news0')['_content'], fixture()['items'][0]['_content'])
        with self.assertRaises(HTTPException) as error: service.topic('news6')
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(len(service.published_topics()), 6)


if __name__ == '__main__': unittest.main()
