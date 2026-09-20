"""运行：python -m unittest ongeki_gacha.tests.test_growth_core。"""
import json
from pathlib import Path
import unittest

from ..gacha_core import CardInfo
from ..growth_core import (
    MAX_AFFECTION_LEVEL,
    REWARD_MAX_LEVEL,
    affection_level,
    affection_progress,
    build_thresholds,
    duplicate_fragments,
)


class GrowthCoreTests(unittest.TestCase):
    def test_curve_boundaries(self):
        data = json.loads((Path(__file__).parents[1] / "assets/growth/affection_curve.json").read_text(encoding="utf-8"))
        thresholds = tuple(data["thresholds"])
        self.assertEqual(len(thresholds), MAX_AFFECTION_LEVEL + 1)
        self.assertEqual(thresholds[0], 0)
        for level in range(1, MAX_AFFECTION_LEVEL + 1):
            self.assertGreater(thresholds[level], thresholds[level - 1])
            self.assertEqual(affection_level(thresholds[level] - 1, thresholds), level - 1)
            self.assertEqual(affection_level(thresholds[level], thresholds), level)
        self.assertEqual(affection_level(thresholds[-1], thresholds), MAX_AFFECTION_LEVEL)
        self.assertEqual(affection_level(thresholds[-1] + 10**9, thresholds), MAX_AFFECTION_LEVEL)
        for level, points in {10: 3000, 20: 9000, 50: 45000, 100: 165000, 200: 363000}.items():
            self.assertEqual(thresholds[level], points)
        self.assertEqual(thresholds[REWARD_MAX_LEVEL], 4455000)
        step = thresholds[REWARD_MAX_LEVEL] - thresholds[REWARD_MAX_LEVEL - 1]
        for level in range(REWARD_MAX_LEVEL, MAX_AFFECTION_LEVEL + 1):
            self.assertEqual(thresholds[level] - thresholds[level - 1], step)
        level, ratio, current, need = affection_progress(thresholds[5000], thresholds)
        self.assertEqual(level, 5000)
        self.assertEqual(need, step)
        self.assertEqual(current, 0)
        self.assertEqual(ratio, 0)
        level, ratio, current, need = affection_progress(thresholds[-1] + 999, thresholds)
        self.assertEqual(level, MAX_AFFECTION_LEVEL)
        self.assertEqual((ratio, current, need), (1.0, step, step))

    def test_curve_rejects_rounding(self):
        with self.assertRaises(ValueError):
            build_thresholds([1] * 10, [101] * 10)

    def test_duplicates_pay_base_and_more_after_overflow(self):
        """重复卡即给碎片；满星内按基础值，超出满星按更高值。"""
        for rarity, maximum, base, overflow in [("N", 11, 1, 2), ("SSR", 5, 8, 16)]:
            self.assertEqual(duplicate_fragments(rarity, 0, 1, source="draw"), 0)
            self.assertEqual(duplicate_fragments(rarity, 1, 2, source="draw"), base)
            self.assertEqual(duplicate_fragments(rarity, 2, maximum, source="draw"), (maximum - 2) * base)
            for source in ("draw", "checkin", "select_card"):
                self.assertEqual(duplicate_fragments(rarity, maximum - 1, maximum + 3, source=source),
                                 base + 3 * overflow)
                self.assertEqual(duplicate_fragments(rarity, maximum + 3, maximum + 4, source=source), overflow)
            self.assertEqual(duplicate_fragments(rarity, 0, maximum + 5, source="affection_reward"), 0)
        with self.assertRaises(ValueError):
            duplicate_fragments("SSR", 5, 6, source="unknown")

    def test_unknown_mapping_does_not_drop_card(self):
        for value in (None, "invalid", True, -1, 1.5):
            self.assertIsNone(CardInfo.from_dict({"id": 1, "charaId": value}).character_id)
        self.assertEqual(CardInfo.from_dict({"id": 1, "charaId": "1000"}).character_id, 1000)


if __name__ == "__main__":
    unittest.main()
