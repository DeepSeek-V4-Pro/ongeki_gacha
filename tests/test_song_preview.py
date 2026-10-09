"""曲库白名单、完整长音频、多源失败切换与QQ发送边界。所有消息使用Mock。"""
import asyncio
import base64
from dataclasses import replace
from io import BytesIO
import json
import logging
import os
import re
from pathlib import Path
import tempfile
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
import urllib.request
import wave

from PIL import Image
from .. import song_preview as sp
from ..task_catalog import CatalogSong, CatalogChart

try:
    from ..config_model import PreviewConfig
    from ..plugin import OngekiGachaPlugin
except ModuleNotFoundError as exc:
    if exc.name != 'maibot_sdk':
        raise
    PreviewConfig = OngekiGachaPlugin = None


def song(title='Test', artist='Singer', identifier='1'):
    return CatalogSong('ongeki', identifier, title, artist, 15, '15', 15, False, False,
                       'https://example.com/game-jacket.png', (CatalogChart(3, 'std', 'MASTER', '15', 15),))


def wav(seconds=2, rate=16000):
    data = BytesIO()
    with wave.open(data, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b'\0\0' * round(seconds * rate))
    return data.getvalue()


class IdentityTests(unittest.TestCase):
    def test_only_catalog_games_and_exact_artist_version(self):
        catalog = [song(), song(artist='Other', identifier='2'), replace(song(), song_id='(LUN)1'),
                   replace(song(), game='unknown'), song(title='Test (Remix)', identifier='3')]
        self.assertEqual(len(sp.catalog_matches(catalog, 'test')), 2)
        self.assertEqual(sp.catalog_matches(catalog, 'test', artist='Singer'), [song()])
        self.assertFalse(sp.catalog_matches(catalog, 'Not in game'))
        self.assertFalse(sp.catalog_matches(catalog, 'Test', artist='Sing'))
        for title, artists in [('Test (Remix)', ('Singer',)), ('Test', ('Other',)),
                                ('Test', ('Singer', 'Guest')), ('Test', ('Singer (CV:X)',))]:
            self.assertFalse(sp.PreviewTrack('p', title, artists, '', '').matches(song()))
        self.assertTrue(sp.PreviewTrack('p', 'ＴＥＳＴ', ('Singer',), '', '').matches(song()))
        self.assertFalse(sp.PreviewTrack('p', 'Test', ('Singer',), '', '', version='Live').matches(song()))
        self.assertTrue(sp.matching_artists('A、B', ('A', 'B')))
        self.assertFalse(sp.matching_artists('A', ('A', 'B')))

    def test_selection_60_second_expiry_and_isolation(self):
        now = [100]
        store = sp.SelectionStore(lambda: now[0])
        store.put(('g', 'u'), [song()])
        with self.assertRaises(sp.PreviewError):
            store.choose(('other', 'u'), 1)
        with self.assertRaises(sp.PreviewError):
            store.choose(('g', 'other'), 1)
        with self.assertRaises(sp.PreviewError):
            store.choose(('g', 'u'), 2)
        now[0] = 159.9
        self.assertEqual(store.choose(('g', 'u'), 1), song())
        store.put(('g', 'u'), [song()])
        now[0] += 60
        with self.assertRaisesRegex(sp.PreviewError, '超时取消'):
            store.choose(('g', 'u'), 1)
        self.assertFalse(store.pending)

    def test_url_trust_is_limited_to_explicit_service_and_auth_never_redirects(self):
        base = 'http://127.0.0.1:3000'
        sp._public_url(base + '/cloudsearch', trusted_base=base)
        with self.assertRaises(sp.PreviewError):
            sp._public_url('http://127.0.0.1:3001/other', trusted_base=base)
        with patch.object(sp.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(sp.PreviewError):
                sp._public_url('https://evil.example/resource')
        handler = sp._PublicRedirect((), base, authenticated=True)
        with self.assertRaisesRegex(sp.PreviewError, '重定向'):
            handler.redirect_request(urllib.request.Request(base, data=b'cookie=secret'), None,
                                     302, '', {}, 'https://other.example/')

    def test_real_ffmpeg_retains_long_audio_and_rejects_overlong(self):
        with tempfile.TemporaryDirectory() as folder:
            try:
                exe = sp.ffmpeg_executable(os.environ.get('ONGEKI_TEST_FFMPEG', ''))
            except sp.PreviewError:
                self.skipTest('FFmpeg unavailable')
            data = sp.convert_preview(wav(75, 22050), folder, executable=exe, seconds=120)
            self.assertAlmostEqual(sp.validate_wav(data, 120), 75, places=1)
            compressed = sp.encode_qq_voice(data, 75, folder, executable=exe)
            decoded = sp.convert_preview(compressed, folder, executable=exe, seconds=120)
            self.assertAlmostEqual(sp.validate_wav(decoded, 120), 75, delta=.2)
            self.assertLess(len(compressed), len(data) / 3)
            # Verify the host's actual duplicated Base64 request fits within its frame.
            encoded = base64.b64encode(compressed).decode()
            self.assertLess(len(json.dumps({'content': encoded, 'data': encoded})), 16 * 1024 * 1024)
            with self.assertRaisesRegex(sp.PreviewError, '超过配置'):
                sp.convert_preview(wav(62), folder, executable=exe, seconds=60)
            with self.assertRaisesRegex(sp.PreviewError, '可解码音频'):
                sp.convert_preview(b'<html>login required</html>', folder, executable=exe, seconds=120)
            # An upstream playlist cannot make the decoder read another local file.
            local = Path(folder) / 'private.wav'
            local.write_bytes(wav(2))
            playlist = ('#EXTM3U\n#EXTINF:2,Test\n' + local.as_uri() + '\n').encode()
            with self.assertRaises(sp.PreviewError):
                sp.convert_preview(playlist, folder, executable=exe, seconds=120)


@unittest.skipIf(PreviewConfig is None, '需要SDK配置环境')
class ProviderTests(unittest.TestCase):
    def test_netease_cookie_post_only_and_trial_detected(self):
        provider = sp.NeteaseAPIProvider('http://127.0.0.1:3000', 'MUSIC_U=test-secret')
        responses = [
            {'code': 200, 'result': {'songs': [{'id': 1, 'name': 'Test', 'ar': [{'name': 'Singer'}]}]}},
            {'code': 200, 'songs': [{'id': 1, 'name': 'Test', 'ar': [{'name': 'Singer'}], 'dt': 180000}]},
            {'code': 200, 'data': [{'id': 1, 'url': 'http://m.music.126.net/test.mp3', 'freeTrialInfo': {'start': 0}}]},
        ]
        with patch.object(sp, 'fetch_json', side_effect=responses) as fetch:
            result = provider.search(song(), 5, deadline=time.monotonic() + 30)
        self.assertTrue(result[0].is_preview)
        self.assertEqual(result[0].duration, 180)
        self.assertTrue(result[0].url.startswith('https:'))
        for call in fetch.call_args_list:
            self.assertNotIn('test-secret', call.args[0])
            self.assertEqual(call.kwargs['form']['cookie'], 'MUSIC_U=test-secret')
            self.assertEqual(call.kwargs['form']['unblock'], 'false')
        self.assertNotIn('test-secret', repr(PreviewConfig(netease_cookie='test-secret')))

    def test_meting_rechecks_same_id_and_artist_not_album_cover(self):
        base = 'http://127.0.0.1:3001/api'
        row = {'title': 'Test', 'author': 'Singer', 'url': base + '?server=tencent&type=url&id=1&auth=token',
               'pic': 'https://wrong.example/album.png'}
        provider = sp.MetingProvider('tencent', base)
        with patch.object(sp, 'fetch_json', side_effect=[[row], [row]]):
            tracks = provider.search(song(), 5, deadline=time.monotonic() + 30)
            self.assertEqual(len(tracks), 1)
        with patch.object(sp, 'fetch_json', side_effect=[[row], [dict(row, author='Other')]]):
            self.assertEqual(provider.search(song(), 5, deadline=time.monotonic() + 30), [])
        with patch.object(sp, 'fetch_json', side_effect=[[dict(row, url='http://127.0.0.1/private')]]):
            self.assertEqual(provider.search(song(), 5, deadline=time.monotonic() + 30), [])

    def test_deezer_rejects_wrong_contributors_and_marks_short(self):
        detail = {'id': 1, 'title': 'Test', 'contributors': [{'name': 'Singer', 'role': 'Main'}],
                  'preview': 'https://cdnt-preview.dzcdn.net/test.mp3'}
        with patch.object(sp, 'fetch_json', side_effect=[{'data': [{'id': 1, 'title': 'Test'}]}, detail]):
            tracks = sp.DeezerProvider().search(song(), 5, deadline=time.monotonic() + 30)
            self.assertTrue(tracks[0].is_preview)
        detail['contributors'].append({'name': 'Other', 'role': 'Featured'})
        with patch.object(sp, 'fetch_json', side_effect=[{'data': [{'id': 1, 'title': 'Test'}]}, detail]):
            self.assertFalse(sp.DeezerProvider().search(song(), 5, deadline=time.monotonic() + 30))


@unittest.skipIf(PreviewConfig is None, '需要SDK配置环境')
class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = PreviewConfig(providers=['netease', 'deezer'], allow_preview_fallback=True)
        self.service = sp.SongPreviewService(self.temp.name, self.config)
        self.track = sp.PreviewTrack('Test source', 'Test', ('Singer',), 'https://example.com/audio.mp3', '', duration=90)
        self.first = SimpleNamespace(name='first', hosts=(), search=Mock(return_value=[self.track]))
        self.second = SimpleNamespace(name='second', hosts=(), search=Mock(return_value=[self.track]))
        self.service.providers = {'netease': self.first, 'deezer': self.second}
        self.patchers = [patch.object(sp, 'ffmpeg_executable', return_value='fake'),
                         patch.object(sp, 'download', return_value=b'audio'),
                         patch.object(sp, 'convert_preview', return_value=wav(90))]
        self.ffmpeg, self.download, self.convert = [p.start() for p in self.patchers]

    def tearDown(self):
        for p in self.patchers: p.stop()
        self.temp.cleanup()

    def test_failure_fallback_full_cache_and_no_cookie_in_cache(self):
        self.first.search.side_effect = sp.PreviewError('登录失效')
        prepared = self.service.prepare(song())
        self.assertEqual(prepared.duration, 90)
        self.assertIn('完整版', prepared.label)
        self.assertFalse(prepared.is_preview)
        self.service.prepare(song())
        self.assertEqual(self.second.search.call_count, 1)
        self.assertEqual(len(list(Path(self.temp.name).glob('*.preview'))), 1)

    def test_wrong_artist_never_downloads(self):
        self.first.search.return_value = [replace(self.track, artists=('Other',))]
        self.second.search.return_value = []
        with self.assertRaises(sp.PreviewError): self.service.prepare(song())
        self.download.assert_not_called()

    def test_short_fallback_waits_for_full_source(self):
        self.first.search.return_value = [replace(self.track, is_preview=True)]
        self.convert.side_effect = [wav(30), wav(90)]
        self.assertFalse(self.service.prepare(song()).is_preview)
        self.assertEqual(self.second.search.call_count, 1)

    def test_unannounced_trial_is_rejected_by_default(self):
        self.config.allow_preview_fallback = False
        self.convert.return_value = wav(30)
        with self.assertRaisesRegex(sp.PreviewError, '不完整音频'):
            self.service.prepare(song())
        self.second.search.assert_not_called()

    def test_unknown_short_and_deezer_are_never_labelled_full(self):
        self.first.search.return_value = [replace(self.track, duration=0)]
        self.second.search.return_value = []
        self.convert.return_value = wav(30)
        prepared = self.service.prepare(song())
        self.assertTrue(prepared.is_preview)
        self.assertIn('试听片段', prepared.label)
        self.assertNotIn('完整版', prepared.label)

    def test_bad_response_falls_back_and_audio_mismatch_rejected(self):
        self.first.search.side_effect = TypeError('bad json shape')
        self.assertFalse(self.service.prepare(song()).is_preview)
        # Separate identity bypasses the previous successful cache.
        self.convert.return_value = wav(110)
        with self.assertRaisesRegex(sp.PreviewError, '时长不符'):
            self.service.prepare(replace(song(), song_id='another'))

    def test_cover_uses_catalog_and_errors_on_missing_or_invalid(self):
        buffer = BytesIO()
        Image.new('RGB', (100, 100)).save(buffer, 'PNG')
        self.download.return_value = buffer.getvalue()
        data = self.service.cover(song())
        self.assertEqual(self.download.call_args.args[0], song().cover_url)
        self.assertEqual(data, buffer.getvalue())
        self.assertEqual(Image.open(BytesIO(data)).width, 100)
        with self.assertRaisesRegex(sp.PreviewError, '未提供'):
            self.service.cover(replace(song(), cover_url=''))
        self.download.return_value = b'not image'
        with self.assertRaisesRegex(sp.PreviewError, '曲绘解码失败'):
            self.service.cover(song())


@unittest.skipIf(OngekiGachaPlugin is None, '需要SDK命令环境')
class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.sender = SimpleNamespace(image=AsyncMock(return_value=True), custom=AsyncMock(return_value=True))
        self.plugin = OngekiGachaPlugin()
        self.plugin._set_context(SimpleNamespace(paths=SimpleNamespace(runtime_dir=root), send=self.sender,
                                                logger=logging.getLogger('preview-test')))
        self.plugin.set_plugin_config(self.plugin.build_default_config())
        self.plugin._user_id = lambda kw: kw.get('user_id', 'u')
        self.plugin._binding_hint = lambda uid: ''
        self.plugin._display_user_id = lambda uid: uid
        self.plugin._send_text = AsyncMock()
        self.plugin._get_task_catalog = AsyncMock(return_value=[song(), song(artist='Other', identifier='2')])
        self.service = Mock()
        self.service.prepare.return_value = sp.PreparedPreview(wav(90), 'test source', 90, False, True)
        self.service.cover.return_value = b'game jacket'
        self.service.voice.return_value = b'ID3mock compressed voice'
        self.plugin._preview_service = lambda: self.service

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def dispatch(self, text, **kwargs):
        message = '/曲目预览' + (' ' + text if text else '')
        matches = [item for item in self.plugin.get_components() if item['type'] == 'COMMAND'
                   and re.fullmatch(item['metadata']['command_pattern'], message)]
        self.assertEqual(len(matches), 1, message)
        metadata = matches[0]['metadata']
        self.assertEqual(matches[0].get('name', metadata.get('name')), 'ongeki_song_preview')
        self.assertGreaterEqual(metadata.get('timeout_ms', 0), 1_200_000)
        groups = re.fullmatch(metadata['command_pattern'], message).groupdict()
        return await getattr(self.plugin, metadata['handler_name'])(
            stream_id=kwargs.pop('stream_id', 'g'), matched_groups=groups, **kwargs)

    async def test_list_is_image_and_followup_uses_same_user_session(self):
        result = await self.dispatch('Test')
        self.assertIn('图片列表', result[1])
        self.sender.image.assert_awaited_once()
        self.service.prepare.assert_not_called()
        self.assertIn('失败', (await self.dispatch('1', user_id='other'))[1])
        self.assertIn('失败', (await self.dispatch('1', stream_id='other'))[1])
        await self.dispatch('2')
        self.assertEqual(self.service.prepare.call_args.args[0].artist, 'Other')
        self.sender.custom.assert_awaited_once()
        self.assertEqual(self.sender.custom.call_args.args[0], 'voice')
        self.assertEqual(base64.b64decode(self.sender.custom.call_args.args[1]), b'ID3mock compressed voice')
        self.assertEqual(self.sender.custom.call_args.kwargs['rpc_timeout_ms'], 90000)
        self.assertFalse(self.plugin._preview_selections.pending)

    async def test_normal_success_sends_only_original_jacket_and_voice(self):
        await self.dispatch('Test | Singer')
        self.sender.image.assert_awaited_once()
        self.assertEqual(base64.b64decode(self.sender.image.call_args.args[0]), b'game jacket')
        self.sender.custom.assert_awaited_once()
        self.plugin._send_text.assert_not_called()

    async def test_registered_command_whitespace_usage_cancel_and_invalid_artist(self):
        self.assertIn('用法', (await self.dispatch(''))[1])
        self.assertIn('请填写曲名', (await self.dispatch('音击'))[1])
        self.assertIn('需要填写歌手', (await self.dispatch('Test |'))[1])
        await self.dispatch('音击\tTest | Singer')
        self.assertEqual(self.service.prepare.call_args.args[0], song())
        self.plugin._preview_attempts.clear()
        await self.dispatch('音击\nTest | Singer')
        self.assertEqual(self.sender.custom.await_count, 2)
        await self.dispatch('Test')
        self.assertIn('已取消', (await self.dispatch('取消'))[1])
        self.assertIn('没有待选择', (await self.dispatch('1'))[1])

    async def test_image_failure_does_not_start_selection_or_send_voice(self):
        self.sender.image.return_value = False
        result = await self.dispatch('Test')
        self.assertIn('图片发送失败', result[1])
        self.assertFalse(self.plugin._preview_selections.pending)
        self.sender.custom.assert_not_called()

    async def test_outside_catalog_artist_and_expired_selection_do_not_fetch(self):
        for query in ['Unknown', 'Test | Singer (Live)', 'https://example.com/audio.mp3']:
            self.assertIn('没有匹配', (await self.dispatch(query))[1])
        self.plugin._preview_selections = sp.SelectionStore(lambda: 0)
        self.plugin._preview_selections.put(('g', 'u'), [song()])
        self.plugin._preview_selections.clock = lambda: 60
        self.assertIn('超时取消', (await self.dispatch('1'))[1])
        self.service.prepare.assert_not_called()

    async def test_manual_error_has_reason_auto_error_is_silent(self):
        self.service.prepare.side_effect = sp.PreviewError('下载返回HTTP 403')
        self.assertIn('HTTP 403', (await self.dispatch('Test | Singer'))[1])
        self.plugin._send_text.reset_mock()
        self.plugin._preview_attempts.clear()
        await self.plugin._automatic_song_preview('g', 'u', song())
        self.plugin._send_text.assert_not_called()
        self.sender.image.assert_not_called()
        self.sender.custom.assert_not_called()
        self.assertFalse(self.plugin._preview_busy)

    async def test_voice_failure_reason_cover_failure_blocks_voice_and_dedupes(self):
        self.sender.custom.return_value = {'success': False, 'error': '转码失败'}
        self.assertIn('转码失败', (await self.dispatch('Test | Singer', message_id='m'))[1])
        await self.dispatch('Test | Singer', message_id='m')
        self.sender.custom.assert_awaited_once()
        self.plugin._preview_attempts.clear()
        self.sender.custom.reset_mock()
        self.sender.image.return_value = False
        self.assertIn('曲绘发送失败', (await self.dispatch('Test | Singer'))[1])
        self.sender.custom.assert_not_called()

    async def test_successful_ultimate_auto_runs_after_commit_outside_lock_only_once(self):
        from ..gacha_db import GachaDatabase
        self.plugin._db = GachaDatabase(Path(self.temp.name) / 'db')
        self.plugin._db.open()
        self.plugin._lock = asyncio.Lock()
        self.plugin._get_task_catalog.return_value = [song()]
        self.plugin._render_task_card = AsyncMock(return_value=('', '任务已接取', False))
        calls = []
        async def auto(stream, user, selected):
            self.assertFalse(self.plugin._lock.locked())
            self.assertIsNotNone(self.plugin._db.get_task(1))
            calls.append(selected)
        self.plugin._automatic_song_preview = auto
        try:
            for _ in range(2):
                await self.plugin.handle_task_accept(stream_id='g', matched_groups={'kind': '终极', 'game': '音击'})
            self.assertEqual(calls, [song()])
        finally:
            self.plugin._db.close()


if __name__ == '__main__':
    unittest.main()
