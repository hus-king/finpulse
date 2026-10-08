import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from backend.market_sentiment import MarketSentimentService, parse_breadth


def rows():
    return [dict(f12=code, f13=market, f104=100, f105=50, f106=10, f6=100000000, f124=1791423000)
            for code, market in [('000001', 1), ('399001', 0)]]


class BreadthTests(unittest.TestCase):
    def test_complete_pair_and_numeric_strings(self):
        data = rows()
        data[0]['f104'] = '100'
        result = parse_breadth(data)
        self.assertEqual(result['advancing'], 200)
        self.assertEqual(result['total'], 320)
        self.assertEqual(result['turnover_cny'], 200000000)
        self.assertIsNotNone(result['market_as_of'])

    def test_partial_duplicate_and_invalid_fields_are_rejected(self):
        invalid = [rows()[:1], [rows()[0], rows()[0]]]
        for field, value in [('f104', '-'), ('f105', -1), ('f106', 1.5), ('f6', float('nan'))]:
            data = rows()
            data[0][field] = value
            invalid.append(data)
        data = rows()
        del data[0]['f104']
        invalid.append(data)
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_breadth(data)


class SentimentTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_backoff_retains_snapshot_and_force_is_bounded(self):
        clock = [100.0]
        service = MarketSentimentService(clock=lambda: clock[0])
        with patch.object(service, '_fetch', new=AsyncMock(return_value=parse_breadth(rows()))) as fetch:
            original = await service.get_sentiment()
            self.assertEqual(original['status'], 'ok')
            await asyncio.gather(*(service.get_sentiment(force=True) for _ in range(12)))
            self.assertEqual(fetch.await_count, 1)
            clock[0] += 91
            fetch.side_effect = ValueError('invalid')
            stale = await service.get_sentiment()
            self.assertEqual(stale['status'], 'stale')
            self.assertEqual(stale['updated_at'], original['updated_at'])
            self.assertEqual(service.snapshot()['status'], 'stale')
            await asyncio.gather(*(service.get_sentiment(force=True) for _ in range(12)))
            self.assertEqual(fetch.await_count, 2)
            clock[0] += 31
            await service.get_sentiment()
            self.assertEqual(fetch.await_count, 3)

    async def test_unavailable_is_cached_without_fake_score(self):
        service = MarketSentimentService()
        with patch.object(service, '_fetch', new=AsyncMock(side_effect=ValueError('empty'))) as fetch:
            first = await service.get_sentiment()
            self.assertEqual(first['status'], 'unavailable')
            self.assertIsNone(first['score'])
            self.assertEqual(await service.get_sentiment(), first)
            fetch.assert_awaited_once()

    async def test_invalid_primary_endpoint_uses_valid_fallback(self):
        service = MarketSentimentService()
        client = AsyncMock()
        from unittest.mock import Mock
        client.get.side_effect = [Mock(status_code=200, json=lambda: {'data': {'diff': rows()[:1]}}),
                                  Mock(status_code=200, json=lambda: {'data': {'diff': rows()}})]
        with patch('backend.market_sentiment.httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value = client
            result = await service.get_sentiment()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(client.get.await_count, 2)
