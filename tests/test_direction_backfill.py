import contextlib
import io
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from backend.news_direction import documents
from scripts.backfill_direction_data import cleaned_documents, intervals


class DirectionBackfillTests(unittest.TestCase):
    def test_windows_cover_requested_days_once(self):
        self.assertEqual(list(intervals('2026-09-28', '2026-10-12', 7)), [
            ('2026-09-28', '2026-10-04'), ('2026-10-05', '2026-10-11'), ('2026-10-12', '2026-10-12')])

    def material(self, body=True):
        row = {'url': 'https://finance.sina.com.cn/stock/example.shtml',
               'title': '贵州茅台发布三季度经营数据', 'published_date': '2026-10-01',
               'content': '贵州茅台发布三季度经营数据，说明产品销售情况。' * 10}
        if body:
            row['raw_content'] = '2026年10月1日 10:30\n贵州茅台发布三季度经营数据\n' + row['content']
        return {'results': [row]}

    def test_backfill_keeps_actual_acquisition_time(self):
        captured = '2026-10-22T02:00:00+00:00'
        rows, _ = cleaned_documents({'name': '贵州茅台', 'code': '600519'}, self.material(),
                                    '2026-10-01', '2026-10-07', captured)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['first_seen_at'], captured)
        self.assertEqual(rows[0]['publication_date'], '2026-10-01')
        qualified, _ = documents({'news': rows})
        self.assertEqual(len(qualified), 1)
        self.assertGreater(qualified[0]['observed'], qualified[0]['published'])

    def test_search_snippets_do_not_become_training_bodies(self):
        rows, _ = cleaned_documents({'name': '贵州茅台', 'code': '600519'}, self.material(False),
                                    '2026-10-01', '2026-10-07', '2026-10-22T02:00:00+00:00')
        qualified, rejected = documents({'news': rows})
        self.assertEqual(qualified, [])
        self.assertEqual(rejected['not_full_body'], 1)

    def test_wrong_date_window_cannot_enter_corpus(self):
        rows, reasons = cleaned_documents({'name': '贵州茅台', 'code': '600519'}, self.material(),
                                          '2026-10-02', '2026-10-07', '2026-10-22T02:00:00+00:00')
        self.assertEqual(rows, [])
        self.assertEqual(reasons['out_of_range'], 1)


@unittest.skipUnless(importlib.util.find_spec('httpx'), 'Requires the deployed provider dependencies')
class ResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_reuses_responses_and_preserves_earliest_observation(self):
        from scripts.backfill_direction_data import collect
        material = DirectionBackfillTests().material()
        old_news, _ = cleaned_documents({'name': '贵州茅台', 'code': '600519'}, material,
                                        '2026-10-01', '2026-10-01', '2026-10-02T02:00:00+00:00')
        bar = {'date': '2026-10-01', 'open': 100., 'close': 101., 'high': 102., 'low': 99., 'volume': 1000.}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = {'schema_version': 1, 'exported_at': '2026-10-21T12:00:00+00:00',
                        'news': old_news, 'markets': {'600519': {'raw': [bar], 'adjusted': []}}}
            source = root / 'baseline.json'
            source.write_text(json.dumps(baseline))
            args = SimpleNamespace(root=Path.cwd(), baseline=source, output=root / 'out',
                                   codes=None, end='2026-10-01', news_days=1, market_days=1,
                                   window_days=7, max_search_requests=1)
            with patch('backend.providers.read_config', return_value={}), \
                    patch('backend.providers.tavily', new=AsyncMock(return_value=material)) as search, \
                    patch('scripts.backfill_direction_data.history', new=AsyncMock(return_value=[bar])) as history, \
                    contextlib.redirect_stdout(io.StringIO()):
                await collect(args)
                self.assertEqual(search.await_count, 1)
                self.assertEqual(history.await_count, 2)
                search.reset_mock()
                history.reset_mock()
                await collect(args)
                search.assert_not_awaited()
                history.assert_not_awaited()
            result = json.loads((args.output / 'corpus.json').read_text())
            self.assertEqual(len(result['news']), 1)
            self.assertEqual(result['news'][0]['first_seen_at'], '2026-10-02T02:00:00+00:00')
            self.assertEqual(json.loads(source.read_text()), baseline)
            self.assertTrue(json.loads((args.output / 'collection-report.json').read_text())['complete'])
            args.news_days = 2
            args.max_search_requests = 2
            with patch('backend.providers.read_config', return_value={}):
                with self.assertRaisesRegex(ValueError, 'different plan'):
                    await collect(args)
            args.news_days = 1
            args.max_search_requests = 1
            args.assemble_only = True
            next((args.output / 'news').glob('*.json')).unlink()
            with patch('backend.providers.read_config', return_value={}), \
                    patch('backend.providers.tavily', new=AsyncMock()) as search, \
                    patch('scripts.backfill_direction_data.history', new=AsyncMock()) as history, \
                    contextlib.redirect_stdout(io.StringIO()):
                await collect(args)
                search.assert_not_awaited()
                history.assert_not_awaited()
            report = json.loads((args.output / 'collection-report.json').read_text())
            self.assertFalse(report['complete'])
            self.assertEqual(report['counts']['missing_news_requests'], 1)
            self.assertEqual(report['failures'], [])


if __name__ == '__main__':
    unittest.main()
