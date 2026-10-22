import json
import unittest
from pathlib import Path


class PoolDistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        payload = json.loads(
            (root / "assets/card_data/gacha_pools.json").read_text(encoding="utf-8")
        )
        cls.regular = {
            int(card["card_id"]): str(card.get("rarity") or "")
            for card in payload["regular_pool"]["cards"]
        }
        cls.sign_in = {
            int(card["card_id"]): str(card.get("rarity") or "")
            for card in payload["non_gacha_pool"]["cards"]
        }

    def test_standard_rarity_rewards_are_in_regular_pool(self):
        self.assertIn(104535, self.regular)
        self.assertIn(104725, self.regular)
        self.assertIn(105330, self.regular)
        self.assertEqual(self.regular[104535], "R")
        self.assertEqual(self.regular[104725], "SSR")
        self.assertEqual(self.regular[105330], "SSR")

    def test_only_cards_without_normal_rate_remain_sign_in_only(self):
        self.assertTrue(self.sign_in)
        self.assertEqual(set(self.sign_in.values()), {"SR+"})
        self.assertTrue(set(self.regular).isdisjoint(self.sign_in))


if __name__ == "__main__":
    unittest.main()
