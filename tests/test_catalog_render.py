"""卡册与物品获得图渲染模块的单元测试。"""
from pathlib import Path
import tempfile
import unittest

from ..catalog_render import (
    LIST_TOP,
    OTHER_KEY,
    PAGE_SIZE,
    ROW_HEIGHT,
    buckets,
    index_text,
    page_count,
    page_slice,
    page_text,
    render_catalog_index,
    render_catalog_page,
)
from ..gacha_core import load_cards
from ..growth_catalog import GrowthCatalog
from ..growth_core import MAIN_CHARACTER_IDS
from ..item_render import render_item_gain


class CatalogRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).parents[1]
        cls.cards = load_cards(root / "assets/card_data/card_info_merged.json")
        cls.catalog = GrowthCatalog(root / "assets/growth", cls.cards)
        cls.groups = buckets(cls.cards)

    def test_buckets_cover_every_card_once(self):
        self.assertEqual(
            sum(len(rows) for rows in self.groups.values()),
            len(self.cards.cards),
        )
        self.assertEqual(set(self.groups), {*MAIN_CHARACTER_IDS, OTHER_KEY})
        self.assertEqual(
            list(self.groups)[:-1],
            sorted(MAIN_CHARACTER_IDS),
            "卡册分类顺序必须稳定升序",
        )

    def test_page_slice_clamps_and_pages(self):
        rows = self.groups[1000]
        pages = page_count(rows)
        self.assertEqual(page_slice(rows, 0)[1:], (1, pages))
        self.assertEqual(page_slice(rows, -5)[1], 1)
        self.assertEqual(page_slice(rows, 999)[1], pages)
        for page in range(1, pages + 1):
            self.assertLessEqual(len(page_slice(rows, page)[0]), PAGE_SIZE)

    def test_last_row_stays_inside_surface(self):
        """满页时最后一行徽章不能溢出白色卡片（回归用例）。"""
        last_chip_bottom = LIST_TOP + 40 + (PAGE_SIZE - 1) * ROW_HEIGHT + 45
        surface_bottom = LIST_TOP - 12 + PAGE_SIZE * ROW_HEIGHT + 48
        self.assertGreaterEqual(surface_bottom, last_chip_bottom)

    def test_text_fallbacks_list_ids(self):
        owned = {100001: 2}
        index_lines = index_text(self.catalog, self.groups, owned, partner=1000)
        self.assertEqual(index_lines[0], "卡册总览")
        self.assertTrue(any("星咲 あかり" in line for line in index_lines))
        page_lines = "\n".join(
            page_text("星咲 あかり", self.groups[1000], owned, 1, "星咲 あかり")
        )
        self.assertIn("100001", page_lines)
        self.assertIn("×2", page_lines)
        self.assertIn("未持有", page_lines)

    def test_pages_render_and_clamp(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            index = render_catalog_index(
                self.catalog, self.groups, {}, out / "index.png"
            )
            self.assertTrue(index.is_file())
            path, page, pages = render_catalog_page(
                self.catalog,
                self.groups[1000],
                {},
                out / "page.png",
                name="星咲 あかり",
                query="星咲 あかり",
                page=999,
            )
            self.assertEqual(page, pages)
            self.assertTrue(path.is_file())


class ItemRenderTests(unittest.TestCase):
    def test_empty_or_non_positive_returns_none(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "item.png"
            self.assertIsNone(render_item_gain({}, out))
            self.assertIsNone(
                render_item_gain({"gift_small": 0, "flower_fragment": -1}, out)
            )
            self.assertFalse(out.exists())

    def test_unknown_item_uses_placeholder(self):
        with tempfile.TemporaryDirectory() as temp:
            path = render_item_gain(
                {"mystery_item": 3},
                Path(temp) / "item.png",
                totals={"mystery_item": 8},
            )
            self.assertIsNotNone(path)
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
