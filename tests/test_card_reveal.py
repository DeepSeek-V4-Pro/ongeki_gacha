"""卡牌揭示图：SSR/N 首获与升星渲染。"""
from pathlib import Path
import tempfile
import unittest

from ..card_reveal import _max_attack, _max_level, render_card_reveal
from ..gacha_core import CardInfo, load_cards
from ..gacha_render import GachaRenderer


class CardRevealTests(unittest.TestCase):
    def test_max_level_tracks_limit_break_stars(self):
        for rarity, slots, final_level in (("N", 11, "100"), ("R", 5, "70"),
                                           ("SR", 5, "70"), ("SRPlus", 5, "70"),
                                           ("SSR", 5, "70")):
            card = CardInfo(1, "test", rarity, "test.png", level_param="60,257,280")
            self.assertEqual(_max_level(card, 1), "10")
            self.assertEqual(_max_level(card, slots), "10")
            self.assertEqual(_max_level(card, 1, is_kaika=True), "50")
            self.assertEqual(_max_level(card, 2, is_kaika=True), "55")
            self.assertEqual(_max_level(card, slots, is_kaika=True), final_level)
            self.assertEqual(_max_level(card, slots + 1, is_kaika=True), final_level)

    def test_attack_uses_card_level_parameters(self):
        sr = CardInfo(1, "SR", "SR", "test.png",
                      level_param="50,222,237,252,267,282,0,0,0,282")
        self.assertEqual(_max_attack(sr, 1), "81")
        self.assertEqual(_max_attack(sr, 1, is_kaika=True), "222")
        self.assertEqual(_max_attack(sr, 3, is_kaika=True), "252")
        ssr = CardInfo(2, "SSR", "SSR", "test.png",
                       level_param="60,257,280,295,307,317,0,0,0,322")
        self.assertEqual(_max_attack(ssr, 5, is_kaika=True), "317")
        self.assertEqual(_max_attack(ssr, 5, is_kaika=True, is_cho_kaika=True), "322")
        normal = CardInfo(3, "N", "N", "test.png",
                          level_param="50,197,212,227,242,257,287,317,347,347")
        self.assertEqual(_max_attack(normal, 6, is_kaika=True), "272")
        self.assertEqual(_max_attack(normal, 11, is_kaika=True), "347")
        missing = CardInfo(4, "missing", "SR", "test.png")
        self.assertEqual(_max_attack(missing, 1), "-")
        reference = load_cards(Path(__file__).parents[1] / 'assets/card_data/card_info_merged.json').by_id[102960]
        self.assertEqual(_max_level(reference, 1), "10")
        self.assertEqual(_max_attack(reference, 1), "81")

    def test_render_new_and_star_up(self):
        root = Path(__file__).parents[1]
        cards = load_cards(root / "assets/card_data/card_info_merged.json")
        renderer = GachaRenderer(
            root / "assets/card_data",
            root / "assets/ui",
        )
        ssr = next(card for card in cards.cards if card.rarity == "SSR" and card.character_id == 1000)
        with tempfile.TemporaryDirectory() as temp:
            new_path = Path(temp) / "new.png"
            star_path = Path(temp) / "star.png"
            new_bytes = render_card_reveal(
                renderer,
                ssr,
                1,
                new_path,
                mode="new",
                character_name="星咲 あかり",
            )
            star_bytes = render_card_reveal(
                renderer,
                ssr,
                2,
                star_path,
                mode="star_up",
                before_copies=1,
                character_name="星咲 あかり",
            )
            self.assertTrue(new_bytes.startswith(b"\x89PNG"))
            self.assertTrue(star_bytes.startswith(b"\x89PNG"))
            self.assertTrue(new_path.is_file() and star_path.is_file())


if __name__ == "__main__":
    unittest.main()
