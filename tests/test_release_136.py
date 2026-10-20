"""1.3.6：曲库保留未解锁曲目、搜索与压缩档位、配置补齐和版本补偿。"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .. import song_preview as sp
from ..compensation_commands import CompensationCommandsMixin
from ..config_model import OngekiGachaPluginConfig
from ..config_sync import sync_json_config, sync_toml_config
from ..gacha_core import load_cards
from ..gacha_db import GachaDatabase
from ..task_catalog import _normalize_lxns, pick_random_task
from ..version_rewards import VERSION_REWARDS, pending_rewards

ROOT = Path(__file__).parents[1]


def lxns_song(**extra):
    base = {
        'id': 7,
        'title': 'Xaleid\u25c6scopiX',
        'artist': 'xi',
        'difficulties': {'dx': [
            {'difficulty': 3, 'level': '14+', 'level_value': 14.9},
        ]},
    }
    base.update(extra)
    return base


class Catalog136Tests(unittest.TestCase):
    def test_locked_and_disabled_songs_stay_searchable_but_not_taskable(self):
        songs = _normalize_lxns('maimai', [lxns_song(locked=True)], '')
        self.assertEqual(len(songs), 1)
        self.assertTrue(songs[0].locked)
        self.assertFalse(songs[0].disabled)
        for kind in ('normal', 'challenge', 'advanced', 'ultimate'):
            self.assertIsNone(pick_random_task(songs, kind, ultimate_min_level=14.0))

        disabled = _normalize_lxns('maimai', [lxns_song(id=8, disabled=True)], '')
        self.assertEqual(len(disabled), 1)
        self.assertTrue(disabled[0].disabled)
        self.assertIsNone(pick_random_task(disabled, 'normal'))

    def test_song_without_scoring_charts_is_kept_for_preview(self):
        raw = lxns_song(id=9, difficulties=[{'difficulty': 5, 'level': '0', 'level_value': 0}])
        songs = _normalize_lxns('chunithm', [raw], '')
        self.assertEqual(len(songs), 1)
        self.assertTrue(songs[0].disabled)
        self.assertEqual(songs[0].charts, ())


class Search136Tests(unittest.TestCase):
    def test_feat_in_provider_title_is_tolerated_but_versions_are_not(self):
        self.assertTrue(sp.titles_match('Cosmic Train', 'Cosmic Train (feat. SUIMI)'))
        self.assertTrue(sp.titles_match('SNIPE WHOLE', 'SNIPE WHOLE (feat. Mei Ayakura)'))
        self.assertFalse(sp.titles_match('Believe the Rainbow', 'Believe the Rainbow (LONG Version)'))
        self.assertFalse(
            sp.titles_match('Believe the Rainbow',
                            'Believe the Rainbow (feat. Sana) [LONG Version]')
        )
        self.assertFalse(sp.titles_match('Genesis', 'Genesis(long ver.)'))

    def test_symbol_only_titles_keep_their_symbols(self):
        self.assertEqual(sp.search_normalized('\u2200'), '\u2200')
        self.assertEqual(sp.search_normalized('+\u2642'), '+\u2642')
        self.assertNotEqual(sp.search_normalized('\u2200'), '')

    def test_artist_rank_cross_script_guest_and_conflict(self):
        self.assertTrue(sp.artists_compatible('Shoichiro Hirata feat.Sana', ('平田祥一郎', '新谷早苗')))
        self.assertFalse(sp.artists_compatible('Shoichiro Hirata feat.Sana', ('平田祥一郎', '新谷早苗'))[1])
        self.assertTrue(sp.artists_compatible('HoneyWorks', ('HoneyWorks', 'v flower')))
        self.assertTrue(sp.artists_compatible('Morrigan feat.Lily', ('Morrigan(WAVE)',))[0])
        self.assertFalse(sp.artists_compatible('Singer', ('Other',))[0])
        self.assertFalse(sp.artists_compatible('Singer', ('X', 'Y'))[0])
        self.assertEqual(sp.artist_rank('', ('Singer',)), 1)
        self.assertTrue(sp.voice_mode(True) == 'full' and sp.voice_mode(False) == 'none')
        self.assertEqual(sp.voice_mode('half'), 'half')


class HalfCompressionTests(unittest.TestCase):
    def _encode(self, duration, *, mode='half', half_bitrate=64000, payload=b'ID3' * 400):
        captured = {}

        class Process:
            returncode = 0

        def fake_run(command, **kwargs):
            captured['command'] = command
            Path(command[-1]).write_bytes(payload)
            return Process()

        folder = tempfile.mkdtemp()
        with patch.object(sp.subprocess, 'run', side_effect=fake_run):
            voice = sp.encode_qq_voice(b'wav', duration, folder, executable='ffmpeg',
                                       mode=mode, half_bitrate=half_bitrate)
        return voice, captured['command']

    def test_half_short_song_keeps_full_length(self):
        voice, command = self._encode(120)
        self.assertEqual(voice.mode, 'half')
        self.assertFalse(voice.truncated)
        self.assertNotIn('-t', command)
        self.assertEqual(voice.note, '')

    def test_half_long_song_keeps_prefix_and_reports_it(self):
        voice, command = self._encode(1000, half_bitrate=64000)
        self.assertTrue(voice.truncated)
        self.assertIn('-t', command)
        kept = float(command[command.index('-t') + 1])
        self.assertLess(kept, 1000)
        self.assertGreater(kept, 600)
        self.assertIn('半压缩', voice.note)
        self.assertIn('64kbps', voice.note)

    def test_full_mode_has_no_truncation(self):
        voice, command = self._encode(1000, mode='full')
        self.assertFalse(voice.truncated)
        self.assertNotIn('-t', command)


class ConfigSyncTests(unittest.TestCase):
    def test_toml_sync_only_touches_numbers(self):
        defaults = OngekiGachaPluginConfig().model_dump()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.toml'
            path.write_text(
                '[plugin]\nenabled = true\nconfig_version = "1.3.5"\n\n'
                '[admin]\nadmin_ids = [123456789]\nallow_local_operator = false\n\n'
                '[pool]\nrotation_mode = "cycle"\nrotation_interval_days = 15\n\n'
                '[economy]\nstreak_cycle_days = 15\nstreak_cycle_reward = 1200\n\n'
                '[preview]\nproviders = ["netease"]\nnetease_cookie = "MUSIC_U=x"\n'
                'compress_media = true\n',
                encoding='utf-8',
            )
            summary = sync_toml_config(path, defaults, '1.3.6')
            self.assertTrue(summary['changed'])
            self.assertTrue(summary['backup'] and Path(summary['backup']).is_file())
            import tomllib
            data = tomllib.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['plugin']['config_version'], '1.3.6')
            # 数值调整生效；管理员、音源、Cookie 原样保留。
            self.assertEqual(data['pool']['rotation_interval_days'], 30)
            self.assertEqual(data['pool']['rotation_mode'], 'cycle')
            self.assertEqual(data['economy']['streak_cycle_days'], 30)
            self.assertEqual(data['economy']['streak_cycle_reward'], 2400)
            self.assertEqual(data['preview']['compress_media'], 'full')
            self.assertEqual(data['preview']['half_bitrate_kbps'], 64)
            self.assertEqual(data['preview']['providers'], ['netease'])
            self.assertEqual(data['preview']['netease_cookie'], 'MUSIC_U=x')
            self.assertEqual(data['admin']['admin_ids'], [123456789])
            # 非数值键不会被补写，也不会新建分组。
            self.assertNotIn('task', data)
            self.assertNotIn('assets', data)
            self.assertFalse([item for item in summary['added']
                              if item.startswith(('admin.', 'preview.providers'))])
            again = sync_toml_config(path, defaults, '1.3.6')
            self.assertFalse(again['changed'])

    def test_customized_defaults_are_not_overwritten(self):
        defaults = OngekiGachaPluginConfig().model_dump()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.toml'
            path.write_text(
                '[pool]\nrotation_mode = "cycle"\nrotation_interval_days = 20\n',
                encoding='utf-8',
            )
            sync_toml_config(path, defaults, '1.3.6')
            import tomllib
            data = tomllib.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['pool']['rotation_mode'], 'cycle')
            self.assertEqual(data['pool']['rotation_interval_days'], 20)

    def test_official_user_keeps_interval(self):
        defaults = OngekiGachaPluginConfig().model_dump()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.toml'
            path.write_text(
                '[plugin]\nconfig_version = "1.3.5"\n\n'
                '[pool]\nrotation_mode = "official"\nrotation_interval_days = 15\n',
                encoding='utf-8',
            )
            sync_toml_config(path, defaults, '1.3.6')
            import tomllib
            data = tomllib.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['pool']['rotation_mode'], 'official')
            self.assertEqual(data['pool']['rotation_interval_days'], 15)

    def test_json_sync_only_touches_numbers(self):
        defaults = OngekiGachaPluginConfig().model_dump()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text(json.dumps({'plugin': {'config_version': '1.3.5'},
                                        'admin': {'admin_ids': ['888']},
                                        'preview': {'compress_media': True,
                                                    'providers': ['netease'],
                                                    'netease_cookie': 'MUSIC_U=y'}}),
                            encoding='utf-8')
            summary = sync_json_config(path, defaults, '1.3.6')
            self.assertTrue(summary['changed'])
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['plugin']['config_version'], '1.3.6')
            self.assertEqual(data['preview']['compress_media'], 'full')
            self.assertEqual(data['preview']['providers'], ['netease'])
            self.assertEqual(data['preview']['netease_cookie'], 'MUSIC_U=y')
            self.assertEqual(data['admin']['admin_ids'], ['888'])
            self.assertEqual(data['preview']['half_bitrate_kbps'], 64)
            # 未新建 pool / growth 等分组。
            self.assertNotIn('pool', data)
            self.assertNotIn('growth', data)

    def test_json_sync_accepts_and_preserves_utf8_bom(self):
        # AstrBot 写出的 JSON 带 UTF-8 BOM，同步不能因此失败或丢掉 BOM。
        defaults = OngekiGachaPluginConfig().model_dump()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            payload = json.dumps({'plugin': {'config_version': '1.3.5'}}, ensure_ascii=False)
            path.write_bytes(b'\xef\xbb\xbf' + payload.encode('utf-8'))
            summary = sync_json_config(path, defaults, '1.3.6')
            self.assertTrue(summary['changed'])
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
            data = json.loads(raw.decode('utf-8-sig'))
            self.assertEqual(data['plugin']['config_version'], '1.3.6')


class CompensationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cards = load_cards(ROOT / 'assets/card_data/card_info_merged.json')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = GachaDatabase(Path(self.temp.name) / 'test.db')
        self.db.open()
        self.db.initialize_growth(self.cards, enabled=True)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def _seed(self, qq_id, created_at):
        with self.db._lock:
            self.db._conn.execute(
                'INSERT INTO players(qq_id, points, created_at, updated_at) VALUES(?,0,?,?)',
                (qq_id, created_at, created_at),
            )
            self.db._conn.execute(
                'INSERT INTO checkins(qq_id, checkin_date, reward, created_at) VALUES(?,?,?,?)',
                (qq_id, created_at[:10], 90, created_at),
            )

    def test_compensation_once_per_version_only_for_existing_players(self):
        baseline = self.db.ensure_version_baseline('1.3.6')
        self._seed('old', '2026-09-01T00:00:00+00:00')
        self._seed('new', '2099-01-01T00:00:00+00:00')
        reward = VERSION_REWARDS[0]
        first = self.db.claim_version_reward('old', '1.3.6', points=reward.points,
                                             items=reward.items, baseline_at=baseline)
        self.assertTrue(first['success'])
        self.assertEqual(first['points'], 2000)
        self.assertEqual(
            self.db.claim_version_reward('old', '1.3.6', points=reward.points,
                                         items=reward.items, baseline_at=baseline)['error'],
            'claimed',
        )
        self.assertEqual(
            self.db.claim_version_reward('new', '1.3.6', points=reward.points,
                                         items=reward.items, baseline_at=baseline)['error'],
            'not_eligible',
        )
        self.assertEqual(
            self.db.claim_version_reward('ghost', '1.3.6', points=reward.points,
                                         items=reward.items, baseline_at=baseline)['error'],
            'no_data',
        )
        with self.db._lock:
            items = {row['item_id']: row['quantity'] for row in self.db._conn.execute(
                "SELECT item_id, quantity FROM player_items WHERE qq_id='old'")}
        self.assertEqual(items['bloom_ticket'], 2)
        self.assertEqual(items['gift_large'], 3)
        self.assertEqual(self.db.claimed_versions('old'), {'1.3.6'})
        self.assertEqual(pending_rewards(self.db.claimed_versions('old')), [])

    def test_baseline_is_stable_across_calls(self):
        first = self.db.ensure_version_baseline('1.3.6')
        self.assertEqual(first, self.db.ensure_version_baseline('1.3.6'))


class _FakeDb:
    def __init__(self, result):
        self.result = result

    def claimed_versions(self, qq_id):
        return set()

    def ensure_version_baseline(self, version):
        return '2026-10-20T00:00:00+00:00'

    def claim_version_reward(self, qq_id, version, *, points, items, baseline_at):
        return dict(self.result)


class _FakePlugin(CompensationCommandsMixin):
    """只装配补偿命令需要的部分，不依赖数据库与素材。"""

    def __init__(self, result, image_ok):
        self._db = _FakeDb(result)
        self._lock = asyncio.Lock()
        self._image_ok = image_ok
        self.texts = []
        self.cards = []

    def _user_id(self, kwargs):
        return 'u'

    async def _send_text(self, stream_id, text, **kwargs):
        self.texts.append(text)

    async def _send_item_gain_card(self, stream_id, user_id, items, *, title='', subtitle=''):
        self.cards.append({'items': dict(items), 'title': title, 'subtitle': subtitle})
        return self._image_ok


class CompensationCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_sends_item_image_without_extra_text(self):
        plugin = _FakePlugin({'success': True, 'points': 6202}, image_ok=True)
        await plugin.handle_version_reward(stream_id='g')
        self.assertEqual(len(plugin.cards), 1)
        self.assertEqual(plugin.cards[0]['items'], {'bloom_ticket': 2, 'gift_large': 3})
        self.assertIn('1.3.6', plugin.cards[0]['title'])
        self.assertIn('+2000', plugin.cards[0]['subtitle'])
        self.assertIn('当前 6202', plugin.cards[0]['subtitle'])
        self.assertEqual(plugin.texts, [])

    async def test_image_failure_falls_back_to_text(self):
        plugin = _FakePlugin({'success': True, 'points': 6202}, image_ok=False)
        await plugin.handle_version_reward(stream_id='g')
        self.assertEqual(len(plugin.cards), 1)
        self.assertEqual(len(plugin.texts), 1)
        self.assertIn('解花券×2', plugin.texts[0])
        self.assertIn('2000 点', plugin.texts[0])

    async def test_ineligible_and_missing_data_send_text_only(self):
        for result, expected in (
            ({'success': False, 'error': 'not_eligible'}, '之前没有你的数据'),
            ({'success': False, 'error': 'no_data'}, '没有你的账号数据'),
        ):
            plugin = _FakePlugin(result, image_ok=True)
            await plugin.handle_version_reward(stream_id='g')
            self.assertEqual(plugin.cards, [])
            self.assertEqual(len(plugin.texts), 1)
            self.assertIn(expected, plugin.texts[0])


if __name__ == '__main__':
    unittest.main()
