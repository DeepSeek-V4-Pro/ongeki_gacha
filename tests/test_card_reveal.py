"""卡牌揭示图：SSR/N 首获与升星渲染。"""
from pathlib import Path
import tempfile
import unittest

from ..card_reveal import render_card_reveal
from ..gacha_core import load_cards
from ..gacha_render import GachaRenderer


class CardRevealTests(unittest.TestCase):
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
