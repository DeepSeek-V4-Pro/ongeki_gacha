"""1.3.3: LU 曲库、分档交易与独立解花券冷却的回归边界。"""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from . import test_growth_service as fixtures
from ..gift_economy import affordable_large_gifts, quote_large_gifts
from ..growth_service import GrowthService
from ..task_catalog import _normalize_ongeki, load_or_fetch_catalog, pick_random_task


class LunaticTests(unittest.TestCase):
    def song(self, internal, **extra):
        return {'title': 'LU', 'songId': 'lu', 'sheets': [
            {'type': 'lun', 'difficulty': 'lunatic', 'level': '14+',
             'levelValue': 14.7, 'internalLevelValue': internal, 'isSpecial': True, **extra}]}

    def test_scored_lu_uses_internal_constant_and_correct_label_even_at_index_zero(self):
        songs = _normalize_ongeki([self.song(14.9)], '')
        self.assertEqual(len(songs), 1)
        chart = songs[0].charts[0]
        self.assertEqual((chart.label, chart.level_value, chart.is_special), ('LUNATIC', 14.9, False))
        for kind in ('normal', 'challenge', 'advanced', 'ultimate'):
            self.assertIsNotNone(pick_random_task(songs, kind, ultimate_min_level=14.8))

    def test_zero_nonfinite_and_unscored_lu_remain_excluded(self):
        for constant in (0, -1, float('nan'), float('inf')):
            self.assertEqual(_normalize_ongeki([self.song(constant)], ''), [])
        self.assertEqual(_normalize_ongeki([self.song(14.9, isScoreValid=False)], ''), [])

    def test_upgrade_refreshes_old_cache_then_uses_new_cache(self):
        songs = _normalize_ongeki([self.song(14.9)], '')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'songs.json'
            path.write_text('[]', encoding='utf8')
            with patch(load_or_fetch_catalog.__module__+'.load_catalog', return_value=songs) as fetch:
                self.assertEqual(load_or_fetch_catalog(path), songs)
                fetch.assert_called_once()
            with patch(load_or_fetch_catalog.__module__+'.load_catalog', side_effect=AssertionError('unexpected fetch')):
                self.assertEqual(load_or_fetch_catalog(path), songs)
            self.assertEqual(json.loads(path.read_text(encoding='utf8'))['version'], 2)


class Economy133Tests(unittest.TestCase):
    setUpClass = classmethod(fixtures.GrowthServiceTests.setUpClass.__func__)
    setUp = fixtures.GrowthServiceTests.setUp
    tearDown = fixtures.GrowthServiceTests.tearDown
    set_item = fixtures.GrowthServiceTests.set_item
    quantity = fixtures.GrowthServiceTests.quantity

    def test_monthly_cross_tier_quote_matches_deduction_and_state(self):
        self.db.get_player('u'); self.set_item(2000)
        first = self.service.exchange_large_gift('u', 9, 'first')
        self.assertEqual(first['price'], 108)
        second = self.service.exchange_large_gift('u', 23, 'second')
        self.assertEqual(second['breakdown'], [
            {'unit_price':12, 'quantity':1, 'cost':12},
            {'unit_price':24, 'quantity':20, 'cost':480},
            {'unit_price':60, 'quantity':2, 'cost':120}])
        self.assertEqual(second['price'], 612)
        self.assertEqual(self.quantity(), 1280)
        state = self.service.gift_purchase_state('u')['large']
        self.assertEqual((state['used'], state['affordable']), (32, 21))
        self.assertIn('剩余 0/10', state['lines'][0])
        self.assertEqual(second, self.service.exchange_large_gift('u',23,'second'))
        self.assertEqual(self.service.gift_purchase_state('u')['large'], state)

    def test_month_reset_uses_configured_timezone_not_day_or_pool_change(self):
        self.db.get_player('u'); self.set_item(5000)
        service = GrowthService(self.db, self.catalog, tz_offset_hours=8)
        clock = datetime(2026, 10, 30, 16, 0, tzinfo=timezone.utc)
        with patch.object(self.db, 'current_date_str', side_effect=lambda offset: clock.astimezone(timezone(timedelta(hours=offset))).date().isoformat()):
            self.assertEqual(service.exchange_large_gift('u',30,'oct')['price'], 600)
            self.db.close(); self.db.open()
            self.db.initialize_growth(self.cards, enabled=True, rules=self.catalog.rules)
            clock = datetime(2026,10,31,15,59,59,tzinfo=timezone.utc)
            self.assertEqual(service.exchange_large_gift('u',1,'oct-last')['price'],60)
            clock += timedelta(seconds=1)
            november = service.exchange_large_gift('u',1,'nov')
            self.assertEqual((november['price'],november['month'],november['monthly_used']),(12,'2026-11',1))
            clock -= timedelta(seconds=1)
            self.assertIn('当前月份',service.exchange_large_gift('u',1,'rollback')['error'])

    def test_insufficient_cross_tier_funds_do_not_consume_quota(self):
        self.db.get_player('u'); self.set_item(131)
        self.assertFalse(self.service.exchange_large_gift('u',11,'poor')['success'])
        self.assertEqual(self.quantity(),131)
        self.assertEqual(self.service.gift_purchase_state('u')['large']['used'],0)
        self.assertTrue(self.service.exchange_large_gift('u',10,'ten')['success'])

    def test_concurrent_exchanges_cannot_both_use_last_discounted_slot(self):
        self.db.get_player('u'); self.set_item(1000)
        self.service.exchange_large_gift('u',9,'first-nine')
        with ThreadPoolExecutor(2) as executor:
            results=list(executor.map(lambda request:self.service.exchange_large_gift('u',1,request),('a','b')))
        self.assertEqual(sorted(result['price'] for result in results),[12,24])
        self.assertEqual(self.service.gift_purchase_state('u')['large']['used'],11)
        self.assertEqual(self.quantity(),856)

    def test_daily_purchase_resets_at_local_midnight_and_preserves_monthly_usage(self):
        self.db.get_player('u'); self.set_item(5000)
        self.db._conn.execute("UPDATE players SET points=10000 WHERE qq_id='u'")
        service = GrowthService(self.db,self.catalog,tz_offset_hours=8)
        clock=datetime(2026,10,7,15,59,59,tzinfo=timezone.utc)
        with patch.object(self.db,'current_date_str',side_effect=lambda offset:clock.astimezone(timezone(timedelta(hours=offset))).date().isoformat()):
            self.assertEqual(service.buy_gift('u','medium',5,'today')['price'],300)
            self.assertFalse(service.buy_gift('u','medium',1,'over')['success'])
            service.exchange_large_gift('u',10,'gifts')
            clock += timedelta(seconds=1)
            self.assertEqual(service.buy_gift('u','medium',5,'tomorrow')['price'],300)
            self.assertEqual(service.gift_purchase_state('u')['large']['used'],10)
            clock -= timedelta(seconds=1)
            self.assertFalse(service.buy_gift('u','medium',1,'rewind')['success'])

    def test_affordability_matches_prices_at_all_boundaries(self):
        for used in (0,9,10,29,30,100):
            for funds in (0,11,12,23,24,59,60,120,599,600,660,10000):
                count=affordable_large_gifts(self.catalog.rules,used,funds)
                cost=sum(p['cost'] for p in quote_large_gifts(self.catalog.rules,used,count))
                next_cost=sum(p['cost'] for p in quote_large_gifts(self.catalog.rules,used,count+1))
                self.assertLessEqual(cost,funds)
                self.assertGreater(next_cost,funds)

    def test_challenge_ticket_exact_30_days_independent_and_idempotent(self):
        clock=datetime(2026,10,7,0,0,tzinfo=timezone.utc)
        def approve(kind,level,grade,index):
            task=self.db.create_task('u',task_kind=kind,game='ongeki',song_id=str(index),song_title='T',
                                     target_level_value=level,challenge_limit=100,advanced_limit=100)
            self.assertTrue(task.success)
            self.db.submit_task(task.task_id,'u')
            result=self.db.approve_task(task.task_id,'admin',grade=grade,reward=100)
            return task.task_id,result
        with patch.object(self.db,'_now_iso',side_effect=lambda:clock.isoformat()):
            self.assertEqual(approve('challenge',10,'SS',0)[1].bloom_tickets,0)
            self.assertEqual(approve('challenge',9.9,'SSS',1)[1].bloom_tickets,0)
            task,first=approve('challenge',10,'SSS',2)
            self.assertEqual(first.bloom_tickets,1)
            self.assertFalse(self.db.approve_task(task,'admin',grade='SSS',reward=100).success)
            self.assertEqual(approve('advanced',13.5,'SSS',3)[1].bloom_tickets,1)
            self.db.close(); self.db.open()
            self.db.initialize_growth(self.cards,enabled=True,rules=self.catalog.rules)
            clock += timedelta(days=30,seconds=-1)
            blocked=approve('challenge',10,'SSS+',4)[1]
            self.assertEqual(blocked.bloom_tickets,0)
            self.assertIn('挑战解花券冷却中',blocked.cooldown_text)
            clock += timedelta(seconds=1)
            self.assertEqual(approve('challenge',10,'SSS+',5)[1].bloom_tickets,1)
            self.assertEqual(self.quantity('bloom_ticket'),3)


if __name__ == '__main__':
    unittest.main()
