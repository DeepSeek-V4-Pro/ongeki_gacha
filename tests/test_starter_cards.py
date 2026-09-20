"""默认主角色、基础N卡来源与初始化重入验证。"""
import unittest
from .test_growth_service import GrowthServiceTests
from ..gacha_core import CardPool
from ..gacha_pools import PoolEntry
from ..starter_cards import STARTER_CARDS, STARTER_CARD_IDS


class StarterTests(GrowthServiceTests):
    def test_default_roster_and_cards_are_idempotent(self):
        first=self.service.snapshot('new')
        self.assertEqual({r['character_id'] for r in first['player_characters']},set(STARTER_CARDS))
        self.assertEqual(first['inventory'],[])
        self.assertTrue(self.service.partner('new',1013,'partner')['success'])
        self.assertTrue(self.service.accompany('new','first-companion')['success'])
        self.assertEqual(self.service.snapshot('new')['inventory'],[])
        granted=self.service.ensure_starter_card('new',1013)
        self.assertTrue(granted['granted'])
        self.assertEqual(granted['copies'],1)
        self.assertEqual(
            {r['card_id']:r['copies'] for r in self.service.snapshot('new')['inventory']},
            {STARTER_CARDS[1013]:1},
        )
        again=self.service.ensure_starter_card('new',1013)
        self.assertFalse(again['granted'])
        self.assertEqual(again['copies'],1)
        self.db.initialize_growth(self.cards,enabled=True,rules=self.catalog.rules)
        self.assertEqual(
            {r['card_id']:r['copies'] for r in self.service.snapshot('new')['inventory']},
            {STARTER_CARDS[1013]:1},
        )

    def test_rewards_reach_eleven_copies_without_gacha(self):
        """初始1张＋节点10张＝11张满星，且这些N卡全程不产生碎片。"""
        self.service.partner('u',1013,'partner')
        self.assertTrue(self.service.ensure_starter_card('u',1013)['granted'])
        conn=self.db._conn
        conn.execute('BEGIN IMMEDIATE')
        self.service._add_points(conn,'u',1013,0,self.catalog.thresholds[-1],self.db._now_iso())
        conn.commit()
        inventory=self.service.snapshot('u')['inventory']
        self.assertEqual(next(r['copies'] for r in inventory if r['card_id']==STARTER_CARDS[1013]),11)
        from ..gacha_core import max_detail_slots
        self.assertEqual(max_detail_slots('N'),11)
        conn.execute('BEGIN IMMEDIATE')
        self.assertEqual(self.service._rewards(conn,'u',1013,self.catalog.thresholds[-1],self.db._now_iso()),[])
        conn.commit()
        self.assertEqual(self.quantity(),0)

    def test_first_view_after_reward_cards_still_grants_base_copy(self):
        self.service.partner('u',1013,'partner')
        conn=self.db._conn
        conn.execute('BEGIN IMMEDIATE')
        self.service._add_points(conn,'u',1013,0,self.catalog.thresholds[-1],self.db._now_iso())
        conn.commit()
        self.assertEqual(self.service.ensure_starter_card('u',1013)['copies'],11)
        self.assertFalse(self.service.ensure_starter_card('u',1013)['granted'])
        self.assertEqual(self.quantity(),0)

    def test_existing_copies_and_affection_survive_restart(self):
        self.service.snapshot('u')
        self.assertTrue(self.service.ensure_starter_card('u',1000)['granted'])
        self.db._conn.execute('UPDATE inventory SET copies=6 WHERE qq_id=? AND card_id=?',('u',STARTER_CARDS[1000]))
        self.points(4500)
        self.db.initialize_growth(self.cards,enabled=True,rules=self.catalog.rules)
        snap=self.service.snapshot('u')
        self.assertEqual(next(r['copies'] for r in snap['inventory'] if r['card_id']==STARTER_CARDS[1000]),6)
        self.assertEqual(next(r['affection_points'] for r in snap['player_characters'] if r['character_id']==1000),4500)

    def test_only_reward_base_n_cards_are_excluded(self):
        self.assertEqual(STARTER_CARD_IDS,{int(r['id']) for c in self.catalog.characters.values() for r in c['rewards'] if r['kind']=='NormalCard'})
        pool=CardPool(self.cards,weight_n=100,weight_r=1,weight_sr=1,weight_sr_plus=1,weight_ssr=1)
        self.assertTrue(all(c.id not in STARTER_CARD_IDS for rows in pool._candidates.values() for c in rows))
        other_n=next(c for c in self.cards.cards if c.rarity=='N' and c.id not in STARTER_CARD_IDS)
        self.assertIn(other_n,pool._candidates['N'])
        raw={'id':'test','cards':[{'card_id':STARTER_CARDS[1000]},{'card_id':other_n.id}]}
        self.assertEqual(set(PoolEntry.from_dict(raw).cards),{other_n.id})


if __name__=='__main__':unittest.main()
