import unittest
from ..profile_render import equipped_rewards
from .test_growth_service import GrowthServiceTests

class PresentationTests(unittest.TestCase):
    def test_only_owned_selected_cosmetics_render(self):
        rows=[{'kind':'NamePlate','id':'p','name':'跨角色名牌'}]
        snapshot={'player_growth_profile':[{'nameplate_id':'p'}],'player_cosmetics':[]}
        self.assertIsNone(equipped_rewards(snapshot,rows)['NamePlate'])
        snapshot['player_cosmetics']=[{'cosmetic_type':'NamePlate','cosmetic_id':'p'}]
        self.assertEqual(equipped_rewards(snapshot,rows)['NamePlate'],rows[0])
        snapshot['player_growth_profile'][0]['nameplate_id']=None
        self.assertIsNone(equipped_rewards(snapshot,rows)['NamePlate'])

class EquipTests(GrowthServiceTests):
    def test_new_rewards_auto_equip_latest(self):
        self.own()
        step = self.catalog.rules["companion_points"]
        self.points(self.catalog.thresholds[-1] - step)
        result = self.service.accompany("u", "auto-equip")
        self.assertTrue(result["success"])
        profile = self.service.snapshot("u")["player_growth_profile"][0]
        latest = {}
        for reward in self.catalog.characters[1000]["rewards"]:
            if reward["kind"] in ("Trophy", "Attachment"):
                latest[reward["kind"]] = reward
        self.assertEqual(profile["title_id"], str(latest["Trophy"]["id"]))
        self.assertEqual(profile["attachment_id"], str(latest["Attachment"]["id"]))

    def test_owned_equipment_can_be_removed_and_replayed(self):
        self.own();conn=self.db._conn
        conn.execute("INSERT INTO player_cosmetics VALUES('u','NamePlate','10001','test','now')")
        self.assertTrue(self.service.equip('u','NamePlate','10001','equip')['success'])
        self.assertEqual(self.service.snapshot('u')['player_growth_profile'][0]['nameplate_id'],'10001')
        first=self.service.equip('u','NamePlate','卸下','remove')
        self.assertTrue(first['success'])
        self.assertEqual(first,self.service.equip('u','NamePlate','卸下','remove'))
        self.assertIsNone(self.service.snapshot('u')['player_growth_profile'][0]['nameplate_id'])
        self.assertFalse(self.service.equip('u','Attachment','10001','invalid')['success'])
