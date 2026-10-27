import asyncio
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from backend.hot_news_matches import HotNewsMatchService, NAMESPACE
from tests import test_hot_stock_impact as impact_fixture


class MatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await impact_fixture.ImpactTests.asyncSetUp(self)
        self.topic['sources'][0]['observed_timestamp'] = 1000
        self.second = deepcopy(self.topic); self.second['id'] = 'topic2'; self.second['title'] += '，供应变化'
        self.board.feed = lambda: {'items': [self.topic, self.second], 'sources': [], 'counts': {}, 'warnings': []}
        self.matching = HotNewsMatchService(self.service, clock=lambda: 1000)

    async def asyncTearDown(self):
        await self.matching.close()
        await impact_fixture.ImpactTests.asyncTearDown(self)

    async def finish(self):
        task = self.matching.tasks.get('alice')
        if task: await asyncio.wait_for(task, 4)

    async def test_one_click_matches_all_selected_news_and_groups_related_stocks(self):
        response = await self.matching.start(['topic1', 'topic2'], 'alice')
        self.assertIn(response['job']['status'], ('queued', 'running', 'completed'))
        task = self.matching.tasks.get('alice')
        if task: await task
        result = self.matching.snapshot('alice')['job']
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['counts']['completed'], 4)
        self.assertEqual(result['counts']['related'], 4)
        self.assertEqual([s['code'] for s in result['stocks']], ['601111', '601857'])
        self.assertEqual(len(result['stocks'][0]['news']), 2)
        self.assertEqual(result['stocks'][0]['news'][0]['analysis']['direction'], 'negative')
        self.assertEqual(len(result['results']), 2)
        self.assertNotIn('_token', result)
        self.assertNotIn('business', result['results'][0]['items'][0])
        self.assertIsNone(self.store.get('lease', self.matching.lease_key('alice')))

    async def test_snapshot_never_generates_and_retry_reuses_private_evidence_cache(self):
        self.assertEqual(self.matching.snapshot('alice'), {'job': None})
        self.completion.assert_not_awaited()
        for attempt in range(2):
            await self.matching.start(['topic1', 'topic2'], 'alice')
            task = self.matching.tasks.get('alice')
            if task: await task
        self.assertEqual(self.completion.await_count, 2)
        before = self.completion.await_count
        self.matching.snapshot('alice'); self.assertEqual(self.completion.await_count, before)
        self.assertEqual(self.matching.snapshot('bob'), {'job': None})

    async def test_duplicate_start_returns_running_job_without_duplicate_work(self):
        gate = asyncio.Event(); original = self.completion.side_effect
        async def complete(*args, **kwargs):
            await gate.wait(); return await original(*args, **kwargs)
        self.completion.side_effect = complete
        first = await asyncio.wait_for(self.matching.start(['topic1', 'topic2'], 'alice'), 1)
        second = await self.matching.start(['topic1'], 'alice')
        self.assertEqual(first['job']['id'], second['job']['id'])
        self.assertEqual(len(self.matching.tasks), 1)
        gate.set(); await self.finish()
        self.assertEqual(self.completion.await_count, 2)

    async def test_empty_watchlist_and_unpublished_ids_reject_before_work(self):
        with self.assertRaises(HTTPException): await self.matching.start(['unknown'], 'alice')
        self.topic['screening']['decision'] = 'exclude'
        with self.assertRaises(HTTPException): await self.matching.start(['topic1'], 'alice')
        self.store.put('watchlist', 'list', [], 'alice')
        with self.assertRaises(HTTPException) as empty: await self.matching.start(['topic2'], 'alice')
        self.assertEqual(empty.exception.status_code, 422); self.completion.assert_not_awaited()

    async def test_partial_failures_are_not_unrelated_and_can_be_retried(self):
        original = self.completion.side_effect
        self.completion.side_effect = None; self.completion.return_value = {'content': 'invalid'}
        await self.matching.start(['topic1'], 'alice'); await self.finish()
        result = self.matching.snapshot('alice')['job']
        self.assertEqual(result['status'], 'partial'); self.assertEqual(result['counts']['failed'], 2)
        self.assertEqual(result['stocks'], [])
        self.completion.side_effect = original
        await self.matching.start(['topic1'], 'alice'); await self.finish()
        self.assertEqual(self.matching.snapshot('alice')['job']['counts']['related'], 2)

    async def test_watchlist_changes_stop_remaining_work_and_hide_removed_stocks(self):
        original = self.completion.side_effect
        async def complete(*args, **kwargs):
            result = await original(*args, **kwargs)
            self.store.put('watchlist', 'list', ['601111'], 'alice')
            return result
        self.completion.side_effect = complete
        await self.matching.start(['topic1', 'topic2'], 'alice'); await self.finish()
        result = self.matching.snapshot('alice')['job']
        self.assertEqual(result['status'], 'interrupted')
        self.assertTrue(result['watchlist_changed'])
        self.assertEqual([s['code'] for s in result['stocks']], ['601111'])
        self.assertEqual(self.completion.await_count, 1)

    async def test_captured_news_finishes_even_if_board_is_refreshed_during_run(self):
        original = self.completion.side_effect
        async def complete(*args, **kwargs):
            result = await original(*args, **kwargs)
            self.board.feed = lambda: {'items': []}
            return result
        self.completion.side_effect = complete
        await self.matching.start(['topic1', 'topic2'], 'alice'); await self.finish()
        self.assertEqual(self.matching.snapshot('alice')['job']['status'], 'completed')
        self.assertEqual(self.completion.await_count, 2)

    async def test_stored_results_survive_new_manager_and_lease_expiry_is_visible(self):
        await self.matching.start(['topic1'], 'alice'); await self.finish()
        other = HotNewsMatchService(self.service, clock=lambda: 1000)
        self.assertEqual(other.snapshot('alice')['job']['counts']['related'], 2)
        record = self.store.get(NAMESPACE, 'latest', 'alice'); record['status'] = 'running'
        self.store.put(NAMESPACE, 'latest', record, 'alice')
        self.assertEqual(other.snapshot('alice')['job']['status'], 'interrupted')

    async def test_shutdown_cancels_job_and_releases_both_leases(self):
        gate = asyncio.Event()
        async def complete(*args, **kwargs): await gate.wait()
        self.completion.side_effect = complete
        await self.matching.start(['topic1'], 'alice')
        while not self.completion.await_count: await asyncio.sleep(.01)
        await self.matching.close()
        self.assertEqual(self.matching.snapshot('alice')['job']['status'], 'interrupted')
        self.assertIsNone(self.store.get('lease', self.matching.lease_key('alice')))
        self.assertEqual(self.service.requests._value, 2)
    async def test_heartbeat_loss_interrupts_without_leaving_requests_occupied(self):
        gate = asyncio.Event()
        async def complete(*args, **kwargs): await gate.wait()
        self.completion.side_effect = complete
        self.matching.heartbeat_seconds = .01
        with patch.object(self.store, 'renew', return_value=False):
            await self.matching.start(['topic1'], 'alice')
            task = self.matching.tasks.get('alice')
            if task:
                with self.assertRaises(asyncio.CancelledError): await asyncio.wait_for(task, 3)
        self.assertEqual(self.matching.snapshot('alice')['job']['status'], 'interrupted')
        self.assertEqual(self.service.requests._value, 2)



if __name__ == '__main__': unittest.main()
