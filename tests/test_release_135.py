# -*- coding: utf-8 -*-
"""1.3.5：曲名异体字/标点搜索，以及只有 LU 谱面的曲目。"""
import unittest
from dataclasses import replace

from ..song_preview import PreviewTrack, _search_text, catalog_matches
from ..task_catalog import CatalogChart, CatalogSong, _normalize_ongeki, pick_random_task


def song(title='Test', artist='Singer', identifier='1'):
    return CatalogSong('ongeki', identifier, title, artist, 15, '15', 15, False, False,
                       'https://example.com/game-jacket.png',
                       (CatalogChart(3, 'std', 'MASTER', '15', 15),))


class Search135Tests(unittest.TestCase):
    def test_traditional_and_simplified_variants_find_japanese_title(self):
        grievance = song(title='怨撃', artist='細江慎治')
        truth = replace(grievance, title='怨撃・真', song_id='(LUN) 怨撃・真')
        catalog = [grievance, truth]
        for query, expected in (('怨擊', grievance), ('怨击', grievance),
                                ('怨撃', grievance), ('怨擊・真', truth)):
            self.assertEqual(catalog_matches(catalog, query), [expected], query)

    def test_punctuation_and_case_are_ignored(self):
        target = song(title='反撃! 突撃! Back To Back!', identifier='2')
        for query in ('反撃 突撃 Back To Back', '反擊！突擊！back to back',
                      '反撃! 突撃! Back-To-Back!'):
            self.assertEqual(catalog_matches([target], query), [target], query)

    def test_game_credit_matches_provider_artist_names(self):
        target = song(title='Test', artist='曲：A & B／歌：C(CV：D)、E(CV：F)', identifier='3')
        self.assertTrue(PreviewTrack('p', 'Test', ('D', 'F'), '', '').matches(target))
        self.assertTrue(PreviewTrack('p', 'Test', ('C',), '', '').matches(target))
        # 音源追加合唱/嘉宾时只要有一位歌手对上就放行（1.3.6 起）。
        self.assertTrue(PreviewTrack('p', 'Test', ('D', 'X'), '', '').matches(target))
        # 两边都是英文名却完全对不上，判为不同演唱阵容。
        self.assertFalse(PreviewTrack('p', 'Test', ('X', 'Y'), '', '').matches(target))
        # 罗马字署名与日文歌手名无法逐字核对，按写法不同放过。
        self.assertTrue(PreviewTrack('p', 'Test', ('平田祥一郎',), '', '')
                        .matches(song(artist='Shoichiro Hirata feat.Sana')))
        self.assertTrue(catalog_matches([target], 'Test', artist='D'))
        self.assertFalse(catalog_matches([target], 'Test', artist='X'))
        self.assertEqual(_search_text(song(title='Test', artist='Singer')), 'Test Singer')
        self.assertEqual(_search_text(target), 'Test')

    def test_lu_only_songs_are_searchable_but_never_task_candidates(self):
        raw = [
            {'songId': '(LUN) 怨撃', 'title': '怨撃', 'artist': '細江慎治',
             'sheets': [{'type': 'lun', 'level': '15', 'internalLevelValue': 15}]},
            {'songId': '(LUN) 怨撃・真', 'title': '怨撃・真', 'artist': '細江慎治',
             'sheets': [{'type': 'lun', 'level': '0', 'internalLevelValue': 0}]},
        ]
        songs = _normalize_ongeki(raw, 'https://example.com')
        self.assertEqual([item.title for item in songs], ['怨撃', '怨撃・真'])
        self.assertFalse(songs[0].disabled)
        self.assertTrue(songs[1].disabled)
        self.assertEqual([item.title for item in catalog_matches(songs, '怨擊')], ['怨撃'])
        self.assertEqual([item.title for item in catalog_matches(songs, '怨擊・真')], ['怨撃・真'])
        zero_only = [songs[1]]
        self.assertIsNone(pick_random_task(zero_only, 'normal'))
        self.assertIsNone(pick_random_task(zero_only, 'ultimate'))


if __name__ == '__main__':
    unittest.main()
