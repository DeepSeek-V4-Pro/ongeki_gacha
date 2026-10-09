"""QQ 曲目预览命令与终极任务的静默自动试听。"""
from __future__ import annotations

import asyncio
import base64
import time
import uuid

from maibot_sdk import Command

from .growth_commands import request_identity
from .song_preview import PreviewError, SelectionStore, SongPreviewService, catalog_matches, require_preview_font
from .task_catalog import GAME_LABELS
from .text_render import render_text_card


GAME_ALIASES = {'音击': 'ongeki', 'ongeki': 'ongeki', '舞萌': 'maimai',
                '舞萌dx': 'maimai', 'maimai': 'maimai', '中二': 'chunithm',
                '中二节奏': 'chunithm', 'chunithm': 'chunithm'}


def sent_success(result):
    return result is True or isinstance(result, dict) and (result.get('success') is True or result.get('sent') is True)


class PreviewCommandsMixin:
    def _init_song_preview(self):
        self._preview_selections = SelectionStore()
        self._preview_busy = set()
        self._preview_attempts = {}
        self._preview_requests = {}

    def _preview_service(self):
        return SongPreviewService(self.ctx.paths.runtime_dir / 'song_previews', self.config.preview)

    @Command('ongeki_song_preview', description='按游戏曲库与歌手版本试听歌曲',
             pattern=r'^/曲目预览(?:\s+(?P<preview_args>[\s\S]+))?\s*$', timeout_ms=1_800_000)
    async def handle_song_preview(self, stream_id: str = '', matched_groups: dict = None, **kwargs):
        user = self._user_id(kwargs)
        key = (stream_id, user)
        raw = str((matched_groups or kwargs.get('matched_groups') or {}).get('preview_args') or '').strip()
        if not self.config.preview.enabled:
            text = '曲目预览未启用'
            await self._send_text(stream_id, text)
            return True, text, True
        if not raw:
            text = '用法：/曲目预览 [音击|舞萌|中二] 曲名 [| 歌手]\n多结果时，60秒内发送 /曲目预览 序号；可发送 /曲目预览 取消'
            await self._send_text(stream_id, text)
            return True, text, True
        if raw == '取消':
            self._preview_selections.cancel(key)
            text = '已取消本次曲目选择'
            await self._send_text(stream_id, text)
            return True, text, True
        if key in self._preview_busy or len(self._preview_busy) >= 3:
            text = '试听正在处理中，请稍后再试'
            await self._send_text(stream_id, text)
            return True, text, True
        request = request_identity(kwargs, stream_id)
        now = time.monotonic()
        self._preview_requests = {k: v for k, v in self._preview_requests.items() if now - v < 120}
        request_key = (user, request)
        if request and request_key in self._preview_requests:
            return True, '该预览请求已处理', True
        if request:
            self._preview_requests[request_key] = now
        self._preview_busy.add(key)
        try:
            if raw.isascii() and raw.isdigit():
                song = self._preview_selections.choose(key, int(raw))
            else:
                self._preview_selections.cancel(key)
                parts = raw.split(maxsplit=1)
                game = GAME_ALIASES.get(parts[0].lower())
                query = (parts[1] if len(parts) > 1 else '') if game else raw
                title, separator, artist = query.partition('|')
                if separator and not artist.strip():
                    raise PreviewError('分隔符 | 后需要填写歌手，或去掉分隔符查看版本列表')
                catalog = await self._get_task_catalog()
                if not catalog:
                    raise PreviewError('游戏曲库获取失败，请稍后重试')
                songs = catalog_matches(catalog, title.strip(), game, artist.strip())
                if not songs:
                    raise PreviewError('三个游戏曲库中没有匹配的曲名/歌手版本；不支持曲库外歌曲或音频链接')
                if len(songs) > 30:
                    raise PreviewError('匹配超过30项，请补全曲名、指定游戏或使用“曲名 | 歌手”缩小范围')
                if len(songs) > 1:
                    await self._send_preview_choices(stream_id, songs)
                    self._preview_selections.put(key, songs)
                    return True, '已发送图片列表，请在60秒内选择序号', True
                song = songs[0]
            # 序号对应的快照仍须属于当前游戏曲库，不能在曲库变更后发送旧条目。
            catalog = await self._get_task_catalog()
            if not catalog or not any(s.key == song.key and s.title == song.title and s.artist == song.artist for s in catalog):
                raise PreviewError('所选歌曲已不在当前曲库，请重新搜索')
            await self._send_song_preview(stream_id, user, song, cover=True)
            return True, f'已发送 {song.title} — {song.artist} 的试听', True
        except PreviewError as exc:
            text = f'曲目预览失败：{exc}'
        except Exception as exc:
            self.ctx.logger.warning('曲目预览异常: %s', type(exc).__name__)
            text = f'曲目预览失败：处理阶段发生 {type(exc).__name__}，请查看插件日志'
        finally:
            self._preview_busy.discard(key)
        await self._send_text(stream_id, text)
        return True, text, True

    async def _send_preview_choices(self, stream_id, songs):
        require_preview_font()
        lines = ['同名曲目需确认歌手版本；60秒内发送 /曲目预览 序号', '']
        for index, song in enumerate(songs, 1):
            lines.extend([f'{index}. [{GAME_LABELS[song.game]}] {song.title}', f'歌手：{song.artist or "曲库未提供"}', ''])
        path = self.ctx.paths.runtime_dir / f'preview_choices_{uuid.uuid4().hex}.png'
        paths = []
        try:
            paths = await asyncio.to_thread(render_text_card, '曲目预览 · 选择版本', lines, path)
            for page in paths:
                result = await asyncio.wait_for(self.ctx.send.image(base64.b64encode(page.read_bytes()).decode(), stream_id,
                    rpc_timeout_ms=self.config.preview.send_timeout_seconds * 1000),
                                                self.config.preview.send_timeout_seconds)
                if not sent_success(result):
                    raise PreviewError('版本列表图片发送失败，未开始选择；请重新搜索')
        except PreviewError:
            raise
        except TimeoutError as exc:
            raise PreviewError('版本列表图片发送超时，未开始选择；请重新搜索') from exc
        except Exception as exc:
            raise PreviewError(f'版本列表图片生成或发送失败（{type(exc).__name__}）') from exc
        finally:
            for page in paths:
                page.unlink(missing_ok=True)

    async def _send_song_preview(self, stream_id, user, song, *, cover):
        now = time.monotonic()
        self._preview_attempts = {key: stamp for key, stamp in self._preview_attempts.items() if now - stamp < 60}
        last = self._preview_attempts.get(user)
        if last is not None and now - last < self.config.preview.cooldown_seconds:
            raise PreviewError('试听请求过于频繁，请稍后再试')
        self._preview_attempts[user] = now
        service = self._preview_service()
        try:
            prepared = await asyncio.wait_for(asyncio.to_thread(service.prepare, song),
                                              self.config.preview.total_timeout_seconds + 70)
        except TimeoutError as exc:
            raise PreviewError('试听音源获取或转换超时') from exc
        except OSError as exc:
            raise PreviewError('试听缓存读写失败，请检查目录权限和磁盘空间') from exc
        try:
            voice = await asyncio.wait_for(asyncio.to_thread(service.voice, prepared), 75)
        except TimeoutError as exc:
            raise PreviewError('QQ长语音压缩超时') from exc
        if cover:
            image = await asyncio.to_thread(service.cover, song)
            try:
                result = await asyncio.wait_for(self.ctx.send.image(base64.b64encode(image).decode(), stream_id,
                    rpc_timeout_ms=self.config.preview.send_timeout_seconds * 1000),
                                                self.config.preview.send_timeout_seconds)
                if not sent_success(result):
                    raise PreviewError('曲绘发送失败，已停止发送试听')
            except TimeoutError as exc:
                raise PreviewError('曲绘发送超时，已停止发送试听') from exc
            except PreviewError:
                raise
            except Exception as exc:
                raise PreviewError(f'曲绘发送失败（{type(exc).__name__}）') from exc
        try:
            result = await asyncio.wait_for(self.ctx.send.custom('voice', base64.b64encode(voice).decode(), stream_id,
                rpc_timeout_ms=self.config.preview.send_timeout_seconds * 1000),
                                            self.config.preview.send_timeout_seconds)
            if not sent_success(result):
                reason = result.get('error', '') if isinstance(result, dict) else ''
                raise PreviewError('QQ语音发送失败' + (f'：{reason}' if reason else '，请检查适配器的语音/转码支持'))
            if prepared.is_preview:
                await self._send_text(stream_id, '曲目预览：' + prepared.label)
        except TimeoutError as exc:
            raise PreviewError('QQ语音发送超时，结果不确定，未自动重试') from exc
        except PreviewError:
            raise
        except Exception as exc:
            raise PreviewError(f'QQ语音发送失败（{type(exc).__name__}）') from exc

    async def _automatic_song_preview(self, stream_id, user, song):
        if not self.config.preview.enabled or not self.config.preview.automatic_ultimate:
            return
        key = (stream_id, user)
        if key in self._preview_busy or len(self._preview_busy) >= 3:
            return
        self._preview_busy.add(key)
        try:
            # 自动入口直接使用已抽到的歌曲及歌手；不重新搜索版本、不弹选择列表。
            await self._send_song_preview(stream_id, user, song, cover=False)
        except Exception as exc:
            self.ctx.logger.debug('终极任务自动试听未发送: %s', type(exc).__name__)
        finally:
            self._preview_busy.discard(key)
