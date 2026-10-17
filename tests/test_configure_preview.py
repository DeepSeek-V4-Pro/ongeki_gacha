# -*- coding: utf-8 -*-
"""曲目预览一键配置脚本的读写、默认值与校验。"""
import json
from pathlib import Path
import tempfile
import tomllib
import unittest

from ..config_model import PreviewConfig
from ..tools import configure_preview as cp


class ConfigurePreviewTests(unittest.TestCase):
    def test_defaults_match_preview_config(self):
        config = PreviewConfig()
        for key in cp.PREVIEW_KEYS:
            self.assertEqual(getattr(config, key), cp.PREVIEW_DEFAULTS[key], key)

    def test_toml_update_preserves_other_sections_and_escapes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'config.toml'
            path.write_text(
                '[plugin]\nenabled = true\nconfig_version = "1.3.5"\n\n'
                '[preview]\nenabled = false\nnetease_cookie = ""\nfuture_key = "keep"\n\n'
                '[ui]\nshort_reply_max_lines = 10\n',
                encoding='utf8')
            values = dict(cp.PREVIEW_DEFAULTS)
            values['netease_cookie'] = 'MUSIC_U=a"b\\c'
            values['ffmpeg_path'] = r'C:\ffmpeg\bin\ffmpeg.exe'
            cp.write_config(path, values)
            data = tomllib.loads(path.read_text(encoding='utf8'))
            self.assertTrue(data['plugin']['enabled'])
            self.assertEqual(data['ui']['short_reply_max_lines'], 10)
            self.assertEqual(data['preview']['netease_cookie'], 'MUSIC_U=a"b\\c')
            self.assertEqual(data['preview']['ffmpeg_path'], r'C:\ffmpeg\bin\ffmpeg.exe')
            self.assertEqual(data['preview']['future_key'], 'keep')
            self.assertTrue(list(Path(temp).glob('config.toml.bak-*')))

    def test_json_update_preserves_unrelated_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'astrbot_plugin_ongeki_gacha_config.json'
            path.write_text('{"plugin": {"enabled": true}, "preview": {"enabled": false, "future_key": 1}}\n',
                            encoding='utf8')
            values = dict(cp.PREVIEW_DEFAULTS)
            values['providers'] = ['netease']
            values['allow_preview_fallback'] = True
            cp.write_config(path, values)
            data = json.loads(path.read_text(encoding='utf8'))
            self.assertTrue(data['plugin']['enabled'])
            self.assertEqual(data['preview']['providers'], ['netease'])
            self.assertTrue(data['preview']['allow_preview_fallback'])
            self.assertEqual(data['preview']['future_key'], 1)
            self.assertIn('netease_cookie', data['preview'])

    def test_invalid_provider_and_url_are_rejected(self):
        for providers in ([], ['unknown'], ['netease', 'netease']):
            with self.assertRaises(ValueError):
                cp.validate_values({**cp.PREVIEW_DEFAULTS, 'providers': providers})
        with self.assertRaises(ValueError):
            cp.validate_values({**cp.PREVIEW_DEFAULTS,
                                'netease_api_url': 'ftp://example.com'})
        with self.assertRaises(ValueError):
            cp.validate_values({**cp.PREVIEW_DEFAULTS,
                                'meting_api_url': 'http://user:pass@example.com/api'})

    def test_secret_is_masked(self):
        self.assertEqual(cp.mask_secret(''), '未设置')
        self.assertEqual(cp.mask_secret('short'), '已设置')
        self.assertEqual(cp.mask_secret('MUSIC_U=abcdefghijklmnop'), 'MUSIC_…mnop')


if __name__ == '__main__':
    unittest.main()
