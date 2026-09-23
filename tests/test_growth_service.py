from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ..gacha_core import load_cards
from ..gacha_db import GachaDatabase
from ..growth_catalog import GrowthCatalog
from ..growth_migration import change_item
from ..growth_service import GrowthService


class GrowthServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).parents[1]
        cls.cards = load_cards(root / "assets/card_data/card_info_merged.json")
        cls.catalog = GrowthCatalog(root / "assets/growth", cls.cards)
        cls.card = next(c for c in cls.cards.cards if c.character_id == 1000 and c.rarity == "SSR")
        cls.other = next(c for c in cls.cards.cards if c.character_id == 1001 and c.rarity == "SSR")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = GachaDatabase(Path(self.temp.name) / "test.db")
        self.db.open()
        self.db.initialize_growth(self.cards, enabled=True, rules=self.catalog.rules)
        self.service = GrowthService(self.db, self.catalog)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_pending_reveal_keeps_bloom_state_at_grant(self):
        from ..starter_cards import STARTER_CARDS
        card_id = STARTER_CARDS[1000]
        self.service.ensure_starter_card('u', 1000)
        self.db._conn.execute(
            "UPDATE inventory SET bloom_stage=1,is_kaika=1 WHERE qq_id='u' AND card_id=?",
            (card_id,),
        )
        self.db._conn.execute('BEGIN IMMEDIATE')
        self.db._grant_card(self.db._conn, 'u', card_id, 'N', 'now', source='affection_reward')
        self.db._conn.execute('COMMIT')
        rows = self.db.list_pending_card_reveals('u')
        self.assertTrue(any(row['after_copies'] == 2 and row['is_kaika']
                            and not row['is_cho_kaika'] for row in rows))

    def test_curve_upgrade_backs_up_already_migrated_database(self):
        import sqlite3
        self.service.snapshot('u')
        self.db._conn.execute("DELETE FROM schema_migrations WHERE migration_id='affection-curve-v2'")
        self.db._conn.execute("UPDATE player_characters SET curve_version='affection-v1', affection_points=16500 WHERE qq_id='u' AND character_id=1000")
        result=self.db.initialize_growth(self.cards,enabled=True,rules=self.catalog.rules)
        self.assertIsNotNone(result['backup_path'])
        backup=sqlite3.connect(result['backup_path'])
        try:
            self.assertEqual(backup.execute("SELECT affection_points FROM player_characters WHERE qq_id='u' AND character_id=1000").fetchone()[0],16500)
        finally:
            backup.close()
        self.assertIsNone(self.db.initialize_growth(self.cards,enabled=True,rules=self.catalog.rules)['backup_path'])

    def own(self, card=None, copies=5):
        card = card or self.card
        return self.db.commit_draw("u", [(card.id, card.rarity)] * copies, cost=0)

    def items(self, amount, item="flower_fragment"):
        conn = self.db._conn
        conn.execute("BEGIN IMMEDIATE")
        change_item(conn, "u", item, amount)
        conn.commit()

    def set_item(self, amount, item="flower_fragment"):
        conn = self.db._conn
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""INSERT INTO player_items(qq_id,item_id,quantity) VALUES('u',?,?)
            ON CONFLICT(qq_id,item_id) DO UPDATE SET quantity=excluded.quantity""", (item, amount))
        conn.commit()

    def points(self, amount):
        self.service.partner("u", 1000, "partner")
        self.db._conn.execute("UPDATE player_characters SET affection_points=? WHERE qq_id='u' AND character_id=1000", (amount,))

    def quantity(self, item="flower_fragment"):
        row = self.db._conn.execute("SELECT quantity FROM player_items WHERE qq_id='u' AND item_id=?", (item,)).fetchone()
        return row[0] if row else 0

    def test_automatic_voice_only_committed_gain_and_reserved_once(self):
        self.own();self.items(4,'gift_small')
        self.assertIsNone(self.service.automatic_voice_event('u','missing'))
        result=self.service.gift('u',1000,'small',1,'gift-auto')
        self.assertTrue(result['success'])
        expected={'character_id':1000,'event':'level_up'}
        self.assertEqual(self.service.automatic_voice_event('u','gift-auto'),expected)
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:self.service.automatic_voice_event('u','gift-auto',claim=True),range(2)))
        self.assertEqual(sum(r is not None for r in results),1)
        restarted=GrowthService(self.db,self.catalog)
        self.assertIsNone(restarted.automatic_voice_event('u','gift-auto',claim=True))
        self.assertIsNone(restarted.automatic_voice_event('other','gift-auto',claim=True))
        self.assertTrue(self.service.partner('u',1000,'partner-only')['success'])
        self.assertIsNone(self.service.automatic_voice_event('u','partner-only',claim=True))

    def test_automatic_voice_gift_without_level_up(self):
        self.own();self.points(self.catalog.thresholds[999]);self.items(1,'gift_small')
        self.assertTrue(self.service.gift('u',1000,'small',1,'small-auto')['success'])
        self.assertEqual(self.service.automatic_voice_event('u','small-auto')['event'],'gift_small')
        failed=self.service.gift('u',1000,'large',1,'failed-auto')
        self.assertFalse(failed['success'])
        self.assertIsNone(self.service.automatic_voice_event('u','failed-auto'))

    def test_no_automatic_bloom_and_all_sources(self):
        # 5 张满星内的重复各给基础碎片（SSR 8），超出满星的两张各给 16
        receipt = self.own(copies=7)
        self.assertEqual(sum(c.fragments for c in receipt.commitments), 64)
        self.assertEqual(self.quantity(), 64)
        self.assertEqual(next(r for r in self.db.get_inventory("u") if r.card_id == self.card.id).bloom_stage, 0)
        self.db.commit_draw("u", [(self.card.id, "SSR")], cost=0, pool_id="test", max_select_points=1)
        claim = self.db.claim_select_card("u", "test", self.card.id, "SSR", max_select_points=1)
        self.assertEqual(claim.fragments, 16)
        self.assertFalse(claim.is_kaika)
        self.assertEqual(self.quantity(), 96)

    def test_bloom_replay_and_concurrency(self):
        self.own()
        self.assertEqual(self.quantity(), 32)
        self.points(self.catalog.thresholds[100])
        self.set_item(5, "bloom_ticket")
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda key: self.service.bloom("u", self.card.id, 1, key), ["a", "b"]))
        self.assertEqual(sum(r["spent"] for r in results), 1)
        self.assertEqual(self.quantity("bloom_ticket"), 4)
        self.points(self.catalog.thresholds[200])
        self.set_item(120, "flower_fragment")
        first = self.service.bloom("u", self.card.id, 2, "super")
        self.assertEqual(first, self.service.bloom("u", self.card.id, 2, "super"))
        self.assertEqual(self.quantity("bloom_ticket"), 4)
        self.assertEqual(self.quantity("flower_fragment"), 30)
        self.own(copies=1)
        self.assertEqual(next(r for r in self.db.get_inventory("u") if r.card_id == self.card.id).bloom_stage, 2)
        self.assertEqual(self.quantity(), 46)

    def test_bloom_stage1_does_not_require_full_stars(self):
        """解花不要求满星，只要求角色好感Lv100＋1张解花券。"""
        self.own(copies=1)
        self.set_item(5, "bloom_ticket")
        self.points(self.catalog.thresholds[100])
        result = self.service.bloom("u", self.card.id, 1, "a")
        self.assertTrue(result["success"], result)
        self.points(self.catalog.thresholds[200])
        result = self.service.bloom("u", self.card.id, 2, "b")
        self.assertIn("满星", result["error"])

    def test_bloom_gate_requires_affection_stars_and_items(self):
        """解花=Lv100＋1券；超解花=已解花＋满星＋Lv200＋90碎片。"""
        self.own()
        self.set_item(10, "bloom_ticket")
        self.set_item(100, "flower_fragment")
        self.points(self.catalog.thresholds[100] - 1)
        result = self.service.bloom("u", self.card.id, 1, "a")
        self.assertIn("角色好感不足", result["error"])
        self.assertEqual(self.quantity("bloom_ticket"), 10)
        self.assertFalse(self.service.bloom("u", self.card.id, 2, "b")["success"])
        self.points(self.catalog.thresholds[100])
        result = self.service.bloom("u", self.card.id, 1, "c")
        self.assertTrue(result["success"], result)
        self.assertEqual(self.quantity("bloom_ticket"), 9)
        result = self.service.bloom("u", self.card.id, 2, "d")
        self.assertIn("角色好感不足", result["error"])
        self.points(self.catalog.thresholds[200])
        result = self.service.bloom("u", self.card.id, 2, "e")
        self.assertTrue(result["success"], result)
        self.assertEqual(self.quantity("bloom_ticket"), 9)
        self.assertEqual(self.quantity("flower_fragment"), 10)

    def test_non_main_and_unknown(self):
        known = next(c for c in self.cards.cards if self.catalog.mapping[c.id]["bloom_policy"] == "material_only" and c.rarity != "N")
        unknown = next(c for c in self.cards.cards if self.catalog.mapping[c.id]["bloom_policy"] == "blocked_unmapped")
        self.own(known)
        self.own(unknown)
        self.set_item(5, "bloom_ticket")
        self.assertTrue(self.service.bloom("u", known.id, 1, "a")["success"])
        self.assertFalse(self.service.bloom("u", unknown.id, 1, "b")["success"])
        self.assertFalse(self.service.partner("u", known.character_id, "c")["success"])
        self.assertFalse(self.service.gift("u", known.character_id, "small", 1, "d")["success"])
        self.assertEqual(self.db._conn.execute("SELECT COUNT(*) FROM player_characters").fetchone()[0], 17)

    def test_daily_global_and_utc(self):
        self.own()
        self.own(self.other)
        self.service.partner("u", 1000, "a")
        with patch.object(self.db, "current_date_str", return_value="2026-09-12"):
            self.assertTrue(self.service.accompany("u", "b")["success"])
            self.service.partner("u", 1001, "c")
            self.assertFalse(self.service.accompany("u", "d")["success"])
        with patch.object(self.db, "current_date_str", return_value="2026-09-13"):
            self.assertTrue(self.service.accompany("u", "e")["success"])

    def test_growth_catalog_accepts_config_overrides(self):
        from ..growth_catalog import GrowthCatalog
        catalog = GrowthCatalog(
            Path(__file__).parents[1] / "assets/growth",
            self.cards,
            rules_override={"bloom_levels": [123, 456], "monthly_event_fragments": 7},
        )
        self.assertEqual(catalog.rules["bloom_levels"], [123, 456])
        self.assertEqual(catalog.rules["monthly_event_fragments"], 7)

    def test_gift_cross_rewards_and_rollback(self):
        self.own()
        self.items(60, "gift_medium")
        fragments = self.quantity()
        before = self.service.snapshot("u")
        with patch.object(self.db, "_grant_card", side_effect=RuntimeError("fault")):
            with self.assertRaises(RuntimeError):
                self.service.gift("u", 1000, "medium", 60, "a")
        self.assertEqual(self.service.snapshot("u"), before)
        result = self.service.gift("u", 1000, "medium", 60, "a")
        self.assertTrue(result["success"])
        self.assertGreaterEqual(len(result["rewards"]), 3)
        self.assertEqual(self.quantity("gift_medium"), 0)
        self.assertEqual(result, self.service.gift("u", 1000, "medium", 60, "a"))
        self.assertFalse(self.service.gift("u", 1000, "medium", 1, "a")["success"])
        self.assertEqual(self.quantity(), fragments)

    def test_gift_and_companion_have_no_cap(self):
        """10 档后无封顶：礼物全额消耗，陪伴照常，累计点可超过 99/99 阈值。"""
        from ..growth_core import MAX_AFFECTION_LEVEL
        self.own()
        top = self.catalog.thresholds[-1]
        self.points(top - 400)
        self.items(2, "gift_small")
        result = self.service.gift("u", 1000, "small", 2, "a")
        self.assertEqual(result["consumed"], 2)
        self.assertEqual(result["returned"], 0)
        self.assertEqual(result["overflow_points"], 0)
        self.assertEqual(self.quantity("gift_small"), 0)
        self.assertEqual(result["points"], top + 200)
        self.assertEqual(result["level"], MAX_AFFECTION_LEVEL)
        result = self.service.accompany("u", "c")
        self.assertTrue(result["success"])
        self.assertEqual(result["gain"], self.catalog.rules["companion_points"])
        self.assertEqual(result["level"], MAX_AFFECTION_LEVEL)
        self.assertEqual(
            result["points"], top + 200 + self.catalog.rules["companion_points"]
        )

    def test_disable_keeps_stage_and_stops_rewards(self):
        self.own()
        self.points(self.catalog.thresholds[100])
        self.set_item(5, "bloom_ticket")
        self.service.bloom("u", self.card.id, 1, "a")
        self.db.initialize_growth(self.cards, enabled=False, rules=self.catalog.rules)
        self.assertFalse(self.service.accompany("u", "b")["success"])
        self.own(copies=5)
        self.assertEqual(self.quantity("bloom_ticket"), 4)
        self.assertEqual(next(r for r in self.db.get_inventory("u") if r.card_id == self.card.id).bloom_stage, 1)

    def test_checkin_monthly_event_and_task_daily_cap(self):
        args = dict(min_reward=100,max_reward=100,streak_daily_step=10,streak_daily_max=100,
                    streak_weekly_reward=500,streak_cycle_days=15,streak_cycle_reward=1000,
                    monthly_daily_bonus=100,tz_offset_hours=0)
        with patch.object(self.db, "current_date_str", return_value="2026-09-01"):
            receipt = self.db.daily_checkin("u", **args)
        self.assertEqual((receipt.small_gifts,receipt.medium_gifts,receipt.large_gifts,receipt.growth_fragments),
                         (1,0,0,0))
        with patch.object(self.db, "current_date_str", return_value="2026-09-01"):
            self.assertFalse(self.db.daily_checkin("u", **args).success)
        conn = self.db._conn
        conn.execute("BEGIN IMMEDIATE")
        # 09-01 已由上面的签到发过，这里覆盖活动周其余日期与活动外的安静日
        event_days = {"2026-09-02":(0,0,5,0),"2026-09-03":(1,0,0,0),
                      "2026-09-05":(0,1,0,0),"2026-09-07":(0,0,0,1),"2026-09-08":(0,0,0,0)}
        for day, expected in event_days.items():
            self.assertEqual(self.db._award_growth_daily(conn,"u",day,"checkin",day,"now"), expected)
        conn.commit()
        self.assertEqual(self.quantity("gift_small"), 2)
        self.assertEqual(self.quantity("gift_medium"), 1)
        self.assertEqual(self.quantity("gift_large"), 1)
        self.assertEqual(self.quantity(), 5)

    def test_balanced_task_items_daily_caps_and_rollback(self):
        self.own();baseline=self.quantity();conn=self.db._conn
        conn.execute('BEGIN IMMEDIATE')
        normal=self.db._award_growth_daily(conn,'u','2026-09-13','normal','n','now')
        challenge=self.db._award_growth_daily(conn,'u','2026-09-13','challenge','c','now')
        advanced=self.db._award_growth_daily(conn,'u','2026-09-13','advanced','a','now')
        ultimate=self.db._award_growth_daily(conn,'u','2026-09-13','ultimate','u','now')
        self.assertEqual(normal,(0,0,1,0))
        self.assertEqual(challenge,(0,1,1,0))
        self.assertEqual(advanced,(0,0,0,0))
        self.assertEqual(ultimate,(0,0,0,0))
        conn.rollback()
        self.assertEqual(self.quantity(),baseline)
        conn.execute('BEGIN IMMEDIATE')
        self.assertEqual(self.db._award_growth_daily(conn,'u','2026-09-13','challenge','c1','now'),(0,1,1,0))
        self.assertEqual(self.db._award_growth_daily(conn,'u','2026-09-13','advanced','a1','now'),(0,0,1,0))
        self.assertEqual(self.db._award_growth_daily(conn,'u','2026-09-13','challenge','c2','now'),(0,0,0,0))
        self.assertEqual(self.db._award_growth_daily(conn,'u','2026-09-13','advanced','a2','now'),(0,0,0,0))
        self.assertEqual(self.db._award_growth_daily(conn,'u','2026-10-13','advanced','a3','now'),(0,1,1,0))
        conn.commit()
        self.assertEqual(self.quantity(),baseline+3)
        self.assertEqual(self.quantity('gift_medium'),2)
        self.assertEqual(self.quantity('gift_large'),0)

    def test_gift_purchase_weekly_cap_and_points(self):
        self.own()
        plans = self.catalog.rules["gift_purchase"]
        self.db._conn.execute("UPDATE players SET points=0 WHERE qq_id='u'")
        self.assertIn('点数不足', self.service.buy_gift('u', 'small', 1, 'poor')['error'])
        self.assertFalse(self.service.buy_gift('u', 'large', 1, 'unsupported')['success'])
        self.db._conn.execute("UPDATE players SET points=5000 WHERE qq_id='u'")
        small, medium = plans['small'], plans['medium']
        first = self.service.buy_gift('u', 'small', small['weekly_cap'], 'buy-1')
        self.assertTrue(first['success'])
        self.assertEqual((first['quantity'], first['price'], first['points']),
                         (small['weekly_cap'], small['price']*small['weekly_cap'],
                          5000-small['price']*small['weekly_cap']))
        self.assertEqual(self.quantity('gift_small'), small['weekly_cap'])
        self.assertFalse(self.service.buy_gift('u', 'small', 1, 'buy-2')['success'])
        self.assertEqual(self.service.buy_gift('u', 'small', small['weekly_cap'], 'buy-1'), first)
        self.assertFalse(self.service.buy_gift('u', 'small', 1, 'buy-1')['success'])
        bought = self.service.buy_gift('u', 'medium', medium['weekly_cap'], 'buy-3')
        self.assertTrue(bought['success'])
        self.assertEqual(bought['price'], medium['price']*medium['weekly_cap'])
        self.assertFalse(self.service.buy_gift('u', 'medium', 1, 'buy-4')['success'])
        self.assertEqual(self.quantity('gift_medium'), medium['weekly_cap'])
        self.assertEqual(self.quantity('gift_large'), 0)

    def test_review_sources_points_and_growth_items(self):
        self.own()
        advanced=self.db.create_task("u",task_kind="advanced",game="maimai",song_id="s0",song_title="T0")
        self.assertTrue(advanced.success)
        self.assertTrue(self.db.submit_task(advanced.task_id,"u").success)
        receipt=self.db.approve_task(advanced.task_id,"admin",grade="SSS",reward=125)
        self.assertTrue(receipt.success)
        self.assertEqual((receipt.medium_gifts,receipt.growth_fragments,receipt.large_gifts),(1,1,0))
        challenge=self.db.create_task("u",task_kind="challenge",game="maimai",song_id="s1",song_title="T1")
        self.assertTrue(challenge.success)
        self.assertTrue(self.db.submit_task(challenge.task_id,"u").success)
        receipt=self.db.approve_task(challenge.task_id,"admin",grade="SSS",reward=80)
        self.assertTrue(receipt.success)
        self.assertEqual((receipt.medium_gifts,receipt.growth_fragments,receipt.large_gifts),(0,1,0))
        normal=self.db.create_task("u",task_kind="normal",game="maimai",song_id="s2",song_title="T2")
        self.assertTrue(self.db.submit_task(normal.task_id,"u").success)
        receipt=self.db.approve_task(normal.task_id,"admin",grade="普通",reward=30)
        self.assertEqual((receipt.medium_gifts,receipt.growth_fragments,receipt.large_gifts),(0,0,0))
        ultimate=self.db.create_task("u",task_kind="ultimate",game="chunithm",song_id="s3",song_title="T3")
        self.assertTrue(self.db.submit_task(ultimate.task_id,"u").success)
        receipt=self.db.complete_ultimate(ultimate.task_id,"admin",reward=30000)
        self.assertTrue(receipt.success)
        self.assertEqual((receipt.medium_gifts,receipt.growth_fragments,receipt.large_gifts),(0,0,0))
        self.assertEqual(receipt.points,30235)
        self.assertEqual(self.quantity("gift_medium"),1)
        self.assertEqual(self.quantity("gift_large"),0)

    def test_bloom_ticket_source_and_cooldown(self):
        def approve(kind, level, grade, song):
            task = self.db.create_task(
                "u", task_kind=kind, game="maimai", song_id=song,
                song_title=song, target_level_value=level,
                normal_limit=100, challenge_limit=100, advanced_limit=100,
            )
            self.assertTrue(task.success)
            self.assertTrue(self.db.submit_task(task.task_id, "u").success)
            return self.db.approve_task(task.task_id, "admin", grade=grade, reward=100)

        challenge = approve("challenge", 13.5, "SSS", "c1")
        self.assertEqual(challenge.bloom_tickets, 0)
        low = approve("advanced", 12.7, "SSS", "a1")
        self.assertEqual(low.bloom_tickets, 0)
        first = approve("advanced", 13.5, "SSS", "a2")
        self.assertEqual(first.bloom_tickets, 1)
        self.assertEqual(self.quantity("bloom_ticket"), 1)
        second = approve("advanced", 13.5, "SSS", "a3")
        self.assertEqual(second.bloom_tickets, 0)
        self.assertIn("冷却", second.cooldown_text)
        self.db._conn.execute(
            "UPDATE player_cooldowns SET last_at='2020-01-01T00:00:00+00:00' WHERE qq_id='u' AND key='bloom_ticket'"
        )
        third = approve("advanced", 13.5, "SSS", "a4")
        self.assertEqual(third.bloom_tickets, 1)
        self.assertEqual(self.quantity("bloom_ticket"), 2)


if __name__ == "__main__":
    unittest.main()
