"""抽卡跨重投、异常退出和数据库重启的结算回归。"""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from . import test_growth_service as fixtures
from ..gacha_db import GachaDatabase


class DrawTransactionTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.GrowthServiceTests.setUpClass.__func__)
    setUp = fixtures.GrowthServiceTests.setUp
    tearDown = fixtures.GrowthServiceTests.tearDown

    def prepare(self):
        self.db.get_player('u')
        self.db._conn.execute("UPDATE players SET points=1000,half_price_5_pull_count=1 WHERE qq_id='u'")
        return SimpleNamespace(pool_id='test',pool_select_points=100,
                               draw=Mock(return_value=[self.card]*5))

    def draw(self, pool, request='request'):
        return self.db.perform_draw('u',pool,5,cost=200,request_id=request)

    def test_concurrent_replay_and_restart_only_charge_once(self):
        pool=self.prepare()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(lambda _:self.draw(pool),range(2)))
        self.assertEqual(results[0],results[1])
        pool.draw.assert_called_once_with(5,guarantee=True)
        self.assertEqual(self.db.get_player('u').points,900)
        self.assertEqual(self.db.get_inventory('u')[0].copies,5)
        self.assertEqual(self.db.get_pool_select_state('u','test').select_points,5)
        self.db.close()
        self.db=GachaDatabase(self.db._db_path)
        self.db.open()
        self.db.initialize_growth(self.cards,enabled=True,rules=self.catalog.rules)
        self.assertEqual(self.draw(pool),results[0])
        self.assertEqual(self.db.get_player('u').points,900)
        pool.draw.assert_called_once()

    def test_failure_after_card_write_rolls_back_qualifications_and_assets(self):
        pool=self.prepare()
        grant=self.db._grant_card
        def fail(*args,**kwargs):
            grant(*args,**kwargs)
            raise RuntimeError('interrupted after card write')
        with patch.object(self.db,'_grant_card',side_effect=fail):
            with self.assertRaises(RuntimeError):self.draw(pool)
        self.assertEqual(self.db.get_player('u').points,1000)
        self.assertEqual(self.db.get_inventory('u'),[])
        self.assertTrue(self.db.half_price_5_pull_available('u'))
        self.assertTrue(self.db.weekly_5_guarantee_available('u',tz_offset_hours=0))
        self.assertEqual(self.db._conn.execute("SELECT COUNT(*) FROM growth_events WHERE source='draw_request'").fetchone()[0],0)
        self.assertTrue(self.draw(pool)['receipt'].success)

    def test_reused_message_with_different_count_is_rejected(self):
        pool=self.prepare()
        self.draw(pool)
        with self.assertRaises(ValueError):
            self.db.perform_draw('u',pool,1,cost=40,request_id='request')
        self.assertEqual(self.db.get_player('u').points,900)
