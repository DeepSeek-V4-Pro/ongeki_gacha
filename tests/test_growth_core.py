"""运行：python -m unittest ongeki_gacha.tests.test_growth_core。"""
import json
from pathlib import Path
import unittest

from ..gacha_core import CardInfo
from ..growth_catalog import GrowthCatalog
from ..growth_core import (
    MAX_AFFECTION_LEVEL,
    REWARD_MAX_LEVEL,
    affection_level,
    affection_progress,
    build_thresholds,
    duplicate_fragments,
)


class GrowthCoreTests(unittest.TestCase):
    def test_monthly_rule_dates_and_task_sources_are_validated(self):
        rules = json.loads((Path(__file__).parents[1] / 'assets/growth/rules_draft.json').read_text(encoding='utf8'))
        for overrides in (
            {'monthly_event_small_gift_days': [1, 1]},
            {'monthly_event_medium_gift_days': [1]},
            {'monthly_event_bloom_ticket_days': [1, 1]},
            {'monthly_event_bloom_ticket_days': [0]},
            {'monthly_event_large_gift_days': [11]},
            {'monthly_event_days': 32},
            {'task_small_gift_sources': [[]]},
            {'task_medium_gift_sources': ['challenge', 'challenge']},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                GrowthCatalog._validate_rules({**rules, **overrides})
        # A ticket may share a gift date; an empty source list disables that task gift.
        GrowthCatalog._validate_rules({**rules, 'monthly_event_bloom_ticket_days': [1, 4, 5],
                                       'task_small_gift_sources': [], 'task_medium_gift_sources': []})

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
        """首获不发碎片；跨越满星边界按各稀有度的新倍率结算。"""
        for rarity, maximum, base, overflow in [
            ("N", 11, 1, 1), ("R", 5, 1, 1), ("SR", 5, 2, 3),
            ("SRPlus", 5, 2, 4), ("SSR", 5, 4, 8),
        ]:
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
