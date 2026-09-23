"""角色专属色和亲密度图标分档回归。"""
import unittest
from unittest.mock import patch

from .. import game_ui, render_theme
from ..tools.build_growth_theme import MANUAL_PRIMARY


class AffectionThemeTests(unittest.TestCase):
    def test_official_character_colors(self):
        self.assertEqual(len(MANUAL_PRIMARY), 17)
        for cid, color in MANUAL_PRIMARY.items():
            self.assertEqual(render_theme.theme_for(cid)['primary'], color)

    def test_decorated_heart_starts_at_level_1000(self):
        for level, expected in ((0, 'GaugeBase'), (10, 'GaugeBase'),
                                (99, 'GaugeBase'), (100, 'GaugeBase'),
                                (999, 'GaugeBase'), (1000, 'GaugeBase_10')):
            with self.subTest(level=level), patch.object(game_ui, '_intimate', return_value=None) as asset:
                game_ui.heart(None, (0, 0, 1, 1), 0, level=level, tier=level // 100)
                self.assertEqual(asset.call_args_list[0].args[0], expected)


if __name__ == '__main__':
    unittest.main()
