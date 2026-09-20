import unittest
from pathlib import Path
from ..gacha_core import load_cards
from ..growth_catalog import GrowthCatalog
from ..character_names import resolve, arguments, name

class CharacterNameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parents[1]
        cls.catalog=GrowthCatalog(root/'assets/growth',load_cards(root/'assets/card_data/card_info_merged.json'))

    def test_all_names_and_legacy_ids(self):
        for cid,c in self.catalog.characters.items():
            for value in (c['name'],name(self.catalog,cid),str(cid)):
                self.assertEqual(resolve(self.catalog,value),cid)
        self.assertEqual(resolve(self.catalog,'星咲明'),1000)
        self.assertEqual(resolve(self.catalog,'樱井春菜'),1007)
        self.assertEqual(resolve(self.catalog,'柏木美亚'),1013)

    def test_arguments_with_spaces(self):
        self.assertEqual(arguments(self.catalog,'送礼','星咲 あかり 小 3'),['1000','小','3'])
        self.assertEqual(arguments(self.catalog,'好感','星咲 明 卡面 100001'),['1000','卡面','100001'])
        self.assertEqual(arguments(self.catalog,'角色语音','星咲 あかり 2'),['1000','2'])
        self.assertEqual(arguments(self.catalog,'角色语音','星咲 あかり'),['1000'])
        self.assertEqual(arguments(self.catalog,'角色语音','星咲明 2'),['1000','2'])
        self.assertEqual(arguments(self.catalog,'养成','100001'),['100001'])

    def test_unknown_and_partial_rejected(self):
        for value in ('柏木','明','46317','不存在',''):
            with self.assertRaises(ValueError):resolve(self.catalog,value)
