"""曲库限定的 QQ 长语音：严格元数据匹配、多音源与一分钟选择。"""
from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
import hashlib
import ipaddress
import json
import math
import re
import shutil
import socket
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import wave
import zipfile

from PIL import Image, ImageFont

from .task_catalog import CatalogSong
from .text_render import FONT_CANDIDATES


class PreviewError(Exception):
    """可直接展示给用户的阶段错误，不包含音源签名 URL。"""


def require_preview_font():
    for path in FONT_CANDIDATES:
        if path.is_file():
            try:
                ImageFont.truetype(str(path), size=22)
                return
            except OSError:
                pass
    raise PreviewError('缺少可用中文字体，请安装 Noto Sans CJK 或接入 assets/fonts；版本选择列表需要图片')


def normalized(value: str) -> str:
    # 不移除括号、feat、CV、混音/现场等版本信息，不猜测艺名或译名。
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value)).casefold())


def matching_artists(expected: str, artists: tuple[str, ...]) -> bool:
    if not expected.strip() or not artists or any(not name.strip() for name in artists):
        return False
    actual = normalized(expected)
    return any(actual == normalized(separator.join(artists))
               for separator in ("、", ", ", " & ", " / ", " × ", " feat. "))


def catalog_matches(catalog, query: str, game: str | None = None, artist: str = ""):
    title = normalized(query)
    if not title:
        raise PreviewError("请填写曲名")
    songs = [song for song in catalog if song.game in {"ongeki", "maimai", "chunithm"}
             and (game is None or song.game == game)]
    # 精确曲名优先；模糊命中只用来生成供用户选择的曲库列表。
    exact = [song for song in songs if normalized(song.title) == title]
    songs = exact or [song for song in songs if title in normalized(song.title)]
    if artist:
        songs = [song for song in songs if normalized(song.artist) == normalized(artist)]
    # 同游戏同曲同歌手的 LU/普通或谱面条目只提供一次试听，优先基础条目。
    unique = {}
    for song in sorted(songs, key=lambda s: (s.song_id.startswith('(LUN)'), s.game, s.song_id)):
        unique.setdefault((song.game, normalized(song.title), normalized(song.artist)), song)
    return list(unique.values())


class SelectionStore:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.pending = OrderedDict()

    def put(self, key, songs):
        self.prune()
        self.pending[key] = (self.clock() + 60, tuple(songs))
        self.pending.move_to_end(key)
        while len(self.pending) > 512:
            self.pending.popitem(last=False)

    def prune(self):
        now = self.clock()
        for key, (expires, _) in list(self.pending.items()):
            if now >= expires:
                self.pending.pop(key, None)

    def cancel(self, key):
        self.pending.pop(key, None)

    def choose(self, key, index):
        selection = self.pending.get(key)
        if selection is None:
            raise PreviewError("没有待选择的列表，或列表已过期；请重新搜索曲名")
        expires, songs = selection
        if self.clock() >= expires:
            self.cancel(key)
            raise PreviewError("选择已超时取消，请重新搜索曲名")
        if not 1 <= index <= len(songs):
            raise PreviewError(f"序号应为 1～{len(songs)}，请在原列表时限内重选")
        self.cancel(key)
        return songs[index - 1]


def origin(url):
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)


def _public_url(url: str, allowed_hosts=(), trusted_base=''):
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or '').lower()
    # 管理员明确配置的自建服务可以在内网；音源返回的任意内网链接仍不被信任。
    if trusted_base and origin(url) == origin(trusted_base) and not parsed.username and not parsed.password:
        if parsed.scheme in ('http', 'https') and host:
            return
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise PreviewError("资源地址不是受支持的 HTTPS 地址")
    if allowed_hosts and not any(host == domain or host.endswith('.' + domain) for domain in allowed_hosts):
        raise PreviewError("音源返回了非预期站点的地址")
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise PreviewError("资源域名解析失败") from exc
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise PreviewError("资源地址不能指向本机或内网")


class _PublicRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts, trusted_base='', authenticated=False):
        self.allowed_hosts = allowed_hosts
        self.trusted_base = trusted_base
        self.authenticated = authenticated

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # 登录 Cookie 在 POST 正文中，只交给指定服务，不跟随重定向。
        if self.authenticated:
            raise PreviewError('登录接口发生重定向，请检查配置的 API 地址')
        newurl = https_media_url(newurl) if not self.trusted_base or origin(newurl) != origin(self.trusted_base) else newurl
        _public_url(newurl, self.allowed_hosts, self.trusted_base)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, *, limit, timeout, allowed_hosts=(), trusted_base='', form=None):
    _public_url(url, allowed_hosts, trusted_base)
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    request = urllib.request.Request(url, data=data, headers={'User-Agent': 'OngekiGacha/1.3.4'})
    try:
        opener = urllib.request.build_opener(_PublicRedirect(allowed_hosts, trusted_base, form is not None))
        started = time.monotonic()
        with opener.open(request, timeout=timeout) as response:
            declared = response.headers.get('Content-Length')
            if declared and int(declared) > limit:
                raise PreviewError("资源超过下载大小上限")
            chunks, size = [], 0
            while True:
                if time.monotonic() - started > timeout:
                    raise PreviewError("资源下载超时")
                chunk = response.read(min(65536, limit + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise PreviewError("资源超过下载大小上限")
                chunks.append(chunk)
        if not size:
            raise PreviewError("资源返回空文件")
        return b''.join(chunks)
    except urllib.error.HTTPError as exc:
        raise PreviewError(f"资源请求返回 HTTP {exc.code}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise PreviewError("资源请求超时") from exc
    except (OSError, ValueError) as exc:
        raise PreviewError("资源网络连接或响应格式异常") from exc


def fetch_json(url, timeout, *, trusted_base='', form=None, array=False):
    try:
        result = json.loads(download(url, limit=2 * 1024 * 1024, timeout=timeout,
                                     trusted_base=trusted_base, form=form))
        if not isinstance(result, list if array else dict):
            raise ValueError()
        return result
    except (ValueError, UnicodeError) as exc:
        raise PreviewError("接口未返回有效 JSON 数据") from exc


def remaining(deadline, timeout):
    budget = deadline - time.monotonic()
    if budget <= 0:
        raise PreviewError('试听资源处理超时')
    return min(timeout, budget)


@dataclass(frozen=True)
class PreviewTrack:
    provider: str
    title: str
    artists: tuple[str, ...]
    url: str
    page_url: str
    version: str = ''
    duration: float = 0
    is_preview: bool = False

    def matches(self, song):
        return (normalized(self.title) == normalized(song.title)
                and matching_artists(song.artist, self.artists)
                and (not self.version or normalized(self.version) in normalized(song.title)))


class DeezerProvider:
    name = 'Deezer'
    hosts = ('dzcdn.net',)

    def search(self, song, timeout, *, deadline):
        url = 'https://api.deezer.com/search?' + urllib.parse.urlencode({'q': song.title, 'limit': 50})
        result = fetch_json(url, remaining(deadline, timeout))
        if result.get('error'):
            raise PreviewError("搜索接口拒绝请求")
        if not isinstance(result.get('data'), list):
            raise PreviewError("搜索响应缺少歌曲列表")
        matches = []
        checked = 0
        for row in result['data']:
            if not isinstance(row, dict) or normalized(row.get('title', '')) != normalized(song.title):
                continue
            identifier = str(row.get('id', ''))
            if not identifier.isdigit():
                continue
            # 搜索页只给主歌手；详情页补全合唱人员，防止选到不同演唱阵容。
            checked += 1
            if checked > 5:
                break
            detail = fetch_json('https://api.deezer.com/track/' + identifier, remaining(deadline, timeout))
            if str(detail.get('id', '')) != identifier:
                continue
            contributors = tuple(item.get('name', '') for item in detail.get('contributors', [])
                                 if item.get('role') in ('Main', 'Featured'))
            if not contributors:
                contributors = (detail.get('artist', {}).get('name', ''),)
            track = PreviewTrack(self.name, detail.get('title', ''), contributors,
                                 detail.get('preview', ''), 'https://www.deezer.com/track/' + identifier,
                                 detail.get('title_version', ''), is_preview=True)
            if track.matches(song) and track.url and detail.get('readable') is not False:
                matches.append(track)
            if len(matches) >= 3:
                break
        return matches


class NeteaseProvider:
    name = '网易云'
    hosts = ('music.163.com', 'music.126.net')

    def search(self, song, timeout, *, deadline):
        url = 'https://music.163.com/api/search/get?' + urllib.parse.urlencode({
            's': song.title + ' ' + song.artist, 'type': 1, 'limit': 50, 'offset': 0})
        result = fetch_json(url, remaining(deadline, timeout))
        body = result.get('result')
        if result.get('code') != 200 or not isinstance(body, dict):
            raise PreviewError("搜索接口不可用或要求验证")
        if not isinstance(body.get('songs', []), list):
            raise PreviewError("搜索响应缺少歌曲列表")
        matches = []
        checked = 0
        for row in body.get('songs', []):
            identifier = str(row.get('id', ''))
            artists = tuple(item.get('name', '') for item in row.get('artists', []))
            if (not identifier.isdigit() or normalized(row.get('name', '')) != normalized(song.title)
                    or not matching_artists(song.artist, artists)):
                continue
            checked += 1
            if checked > 5:
                break
            detail = fetch_json('https://music.163.com/api/song/detail?' + urllib.parse.urlencode(
                {'id': identifier, 'ids': '[' + identifier + ']'}), remaining(deadline, timeout))
            songs = detail.get('songs') or []
            if detail.get('code') != 200 or not songs:
                continue
            item = songs[0]
            if str(item.get('id', '')) != identifier or item.get('fee', 0) in (1, 4) or item.get('status', 0) < 0:
                continue
            track = PreviewTrack(self.name, item.get('name', ''),
                tuple(artist.get('name', '') for artist in item.get('artists', [])),
                f'https://music.163.com/song/media/outer/url?id={identifier}.mp3',
                f'https://music.163.com/song?id={identifier}', duration=float(item.get('duration', 0)) / 1000)
            if track.matches(song):
                matches.append(track)
            if len(matches) >= 3:
                break
        return matches


def https_media_url(url):
    # 国内 CDN 常返回 HTTP；只升级传输协议，不修改资源 ID 或签名。
    return 'https://' + url[7:] if url.startswith('http://') else url


class NeteaseAPIProvider:
    name = '网易云登录接口'
    hosts = ('music.163.com', 'music.126.net')

    def __init__(self, base, cookie):
        self.base = base.rstrip('/')
        self.cookie = cookie

    def search(self, song, timeout, *, deadline):
        if not self.base:
            raise PreviewError('未配置 preview.netease_api_url')

        def api(path, **params):
            # 不使用跨平台解锁/替换功能，以免元数据对应 A、实际音频却来自 B。
            return fetch_json(self.base + path, remaining(deadline, timeout), trusted_base=self.base,
                              form={**params, **({'cookie': self.cookie} if self.cookie else {}), 'unblock': 'false'})

        result = api('/cloudsearch', keywords=song.title + ' ' + song.artist, type=1, limit=50)
        if result.get('code') != 200:
            raise PreviewError('搜索失败，请检查接口与 Cookie 登录状态')
        matches = []
        for row in (result.get('result') or {}).get('songs', [])[:50]:
            identifier = str(row.get('id', ''))
            if not identifier.isdigit() or not PreviewTrack(self.name, row.get('name', ''),
                    tuple(a.get('name', '') for a in row.get('ar', [])), '', '').matches(song):
                continue
            detail = api('/song/detail', ids=identifier)
            rows = detail.get('songs') or []
            if detail.get('code') != 200 or not rows or str(rows[0].get('id', '')) != identifier:
                continue
            item = rows[0]
            track = PreviewTrack(self.name, item.get('name', ''),
                tuple(a.get('name', '') for a in item.get('ar', [])), '', '',
                duration=float(item.get('dt', 0)) / 1000)
            if not track.matches(song):
                continue
            audio = api('/song/url/v1', id=identifier, level='standard')
            rows = audio.get('data') or []
            if audio.get('code') != 200 or not rows or str(rows[0].get('id', '')) != identifier:
                raise PreviewError('播放地址获取失败，请检查 Cookie 与账号播放权限')
            resource = rows[0]
            if not resource.get('url'):
                raise PreviewError('该曲无可用播放地址，请检查 Cookie、账号权限或地区限制')
            from dataclasses import replace
            matches.append(replace(track, url=https_media_url(resource['url']),
                page_url='https://music.163.com/song?id=' + identifier,
                is_preview=resource.get('freeTrialInfo') not in (None, False, 'null', {})))
            if len(matches) >= 3:
                break
        return matches


class MetingProvider:
    """metowolf/Meting-API 的标准接口；各平台 Cookie 由服务端持有。"""
    hosts = ()

    def __init__(self, server, base):
        self.server = server
        self.base = base
        self.name = 'Meting/' + {'netease': '网易云', 'tencent': 'QQ音乐', 'kugou': '酷狗', 'kuwo': '酷我'}[server]

    def search(self, song, timeout, *, deadline):
        if not self.base:
            raise PreviewError('未配置 preview.meting_api_url（登录信息在该服务端配置）')
        def api(kind, identifier):
            parsed = urllib.parse.urlsplit(self.base)
            query = dict(urllib.parse.parse_qsl(parsed.query))
            query.update(server=self.server, type=kind, id=identifier)
            url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
            return fetch_json(url, remaining(deadline, timeout), trusted_base=self.base, array=True)

        result = api('search', song.title + ' ' + song.artist)
        matches = []
        for row in result[:50]:
            if not isinstance(row, dict):
                continue
            track = PreviewTrack(self.name, row.get('title', ''), tuple(row.get('author', '').split(' / ')),
                                 row.get('url', ''), '')
            if not track.matches(song) or not track.url:
                continue
            # 从签名 URL 读取同平台的歌曲 ID，再核对详情，拒绝搜索结果错配。
            parsed = urllib.parse.urlsplit(track.url)
            params = urllib.parse.parse_qs(parsed.query)
            if origin(track.url) != origin(self.base) or params.get('server') != [self.server] or params.get('type') != ['url']:
                continue
            identifier = params.get('id', [''])[0]
            if not identifier or len(identifier) > 128:
                continue
            detail = api('song', identifier)
            for item in detail[:1]:
                candidate = PreviewTrack(self.name, item.get('title', ''), tuple(item.get('author', '').split(' / ')),
                                         item.get('url', ''), '')
                audio_params = urllib.parse.parse_qs(urllib.parse.urlsplit(candidate.url).query)
                if (candidate.matches(song) and origin(candidate.url) == origin(self.base)
                        and audio_params.get('id') == [identifier] and audio_params.get('server') == [self.server]
                        and audio_params.get('type') == ['url']):
                    matches.append(candidate)
            if len(matches) >= 3:
                break
        return matches


def ffmpeg_executable(configured=''):
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise PreviewError("配置的 FFmpeg 不存在")
        return str(path)
    executable = shutil.which('ffmpeg')
    if executable:
        return executable
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as exc:
        raise PreviewError("缺少 FFmpeg；请安装 imageio-ffmpeg 或设置 preview.ffmpeg_path") from exc


def convert_preview(payload, directory, *, executable, seconds):
    with tempfile.TemporaryDirectory(prefix='preview-', dir=directory) as folder:
        output = Path(folder) / 'preview.wav'
        try:
            process = subprocess.run([executable, '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-protocol_whitelist', 'pipe', '-i', 'pipe:0', '-t', str(seconds + 1), '-vn',
                '-map_metadata', '-1', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', '-y', str(output)],
                input=payload, capture_output=True, timeout=60,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as exc:
            raise PreviewError("音频转换超时") from exc
        except OSError as exc:
            raise PreviewError("FFmpeg 无法启动") from exc
        if process.returncode or not output.is_file():
            raise PreviewError("音源不是可解码音频，或 FFmpeg 转换失败")
        data = output.read_bytes()
        validate_wav(data, seconds)
        return data


def encode_qq_voice(data, duration, directory, *, executable):
    """保留全长压缩为 MP3，给 SDK 双份 Base64 字段留出 16MiB 帧预算。"""
    limit = 5 * 1024 * 1024
    budget = limit * 8 / (duration + 10)
    bitrate = max(rate for rate in (8000, 16000, 24000, 32000, 40000, 48000) if rate <= budget)
    Path(directory).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='voice-', dir=directory) as folder:
        path = Path(folder) / 'voice.mp3'
        try:
            process = subprocess.run([executable, '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-protocol_whitelist', 'pipe', '-i', 'pipe:0', '-map_metadata', '-1', '-vn',
                '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame', '-b:a', str(bitrate), '-y', str(path)],
                input=data, capture_output=True, timeout=60,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as exc:
            raise PreviewError('QQ长语音压缩超时') from exc
        except OSError as exc:
            raise PreviewError('QQ长语音编码器无法启动') from exc
        if process.returncode or not path.is_file():
            raise PreviewError('QQ长语音MP3压缩失败，请检查FFmpeg的libmp3lame编码器')
        payload = path.read_bytes()
        if not payload or len(payload) > limit:
            raise PreviewError('QQ长语音压缩后仍超过发送大小上限')
        return payload


def validate_wav(data, seconds):
    try:
        with wave.open(BytesIO(data)) as wav:
            duration = wav.getnframes() / wav.getframerate()
            if duration > seconds + .1:
                raise PreviewError('音频超过配置的最大时长，已取消发送；不会截断冒充完整版')
            if not 0 < duration or wav.getnchannels() != 1 or wav.getframerate() != 16000 or wav.getsampwidth() != 2:
                raise ValueError()
        if len(data) > (seconds + 1) * 32000:
            raise ValueError()
        return duration
    except (wave.Error, EOFError, ValueError, ZeroDivisionError) as exc:
        raise PreviewError("试听音频时长或格式校验失败") from exc


@dataclass(frozen=True)
class PreparedPreview:
    audio: bytes
    provider: str
    duration: float
    is_preview: bool = False
    length_verified: bool = False

    @property
    def label(self):
        kind = '试听片段' if self.is_preview else ('完整版' if self.length_verified else '音源音频（上游未标注全长）')
        return f'{kind} · {int(self.duration)//60}:{int(self.duration)%60:02d} · {self.provider}'


class SongPreviewService:
    def __init__(self, cache_dir, config):
        self.cache_dir = Path(cache_dir)
        self.config = config
        self.providers = {'deezer': DeezerProvider(), 'netease': NeteaseProvider(),
                          'netease_api': NeteaseAPIProvider(config.netease_api_url, config.netease_cookie)}
        for name in ('netease', 'tencent', 'kugou', 'kuwo'):
            self.providers['meting_' + name] = MetingProvider(name, config.meting_api_url)

    def prepare(self, song):
        if not song.artist.strip():
            raise PreviewError("曲库未提供歌手信息，无法严格核对演唱版本")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cleanup()
        key = hashlib.sha256(json.dumps([song.game, song.song_id, song.title, song.artist,
            self.config.max_duration_seconds, self.config.providers, self.config.allow_preview_fallback,
            self.config.netease_api_url, self.config.meting_api_url,
            hashlib.sha256(self.config.netease_cookie.encode()).hexdigest()], ensure_ascii=False).encode()).hexdigest()
        path = self.cache_dir / (key + '.preview')
        try:
            if path.is_file() and time.time() - path.stat().st_mtime < self.config.cache_ttl_seconds:
                with zipfile.ZipFile(path) as archive:
                    if archive.getinfo('audio.wav').file_size > (self.config.max_duration_seconds + 1) * 32000:
                        raise ValueError()
                    if archive.getinfo('info.json').file_size > 4096:
                        raise ValueError()
                    info = json.loads(archive.read('info.json'))
                    data = archive.read('audio.wav')
                duration = validate_wav(data, self.config.max_duration_seconds)
                return PreparedPreview(data, info['provider'], duration, info['is_preview'], info['length_verified'])
        except (PreviewError, OSError, ValueError, KeyError, zipfile.BadZipFile):
            path.unlink(missing_ok=True)
        executable = ffmpeg_executable(self.config.ffmpeg_path)
        deadline = time.monotonic() + self.config.total_timeout_seconds
        errors, fallback = [], None
        for name in self.config.providers:
            provider = self.providers[name]
            if name == 'deezer' and not self.config.allow_preview_fallback:
                continue
            try:
                remaining(deadline, 1)
                tracks = provider.search(song, self.config.request_timeout_seconds,
                                         deadline=min(deadline, time.monotonic() + 35))
                if not tracks:
                    errors.append(f'{provider.name}：无曲名、歌手及版本完全匹配的音源')
                    continue
                for track in tracks:
                    if not track.matches(song):
                        continue
                    try:
                        if not math.isfinite(track.duration) or track.duration < 0:
                            raise PreviewError('音源时长元数据无效')
                        if track.is_preview and not self.config.allow_preview_fallback:
                            raise PreviewError('仅有试听片段，请配置有效登录状态或其他音源')
                        payload = download(track.url, limit=self.config.max_download_bytes,
                            timeout=remaining(deadline, self.config.download_timeout_seconds),
                            allowed_hosts=provider.hosts,
                            trusted_base=provider.base if isinstance(provider, MetingProvider) else '')
                        remaining(deadline, 1)
                        data = convert_preview(payload, self.cache_dir, executable=executable,
                                               seconds=self.config.max_duration_seconds)
                        duration = validate_wav(data, self.config.max_duration_seconds)
                        tolerance = max(5, track.duration * .03)
                        if track.duration and duration > track.duration + tolerance:
                            raise PreviewError('实际音频与元数据时长不符，无法确认版本')
                        short = track.is_preview or (track.duration > 0 and duration < track.duration - tolerance)
                        # Meting 标准响应不提供全长，短文件无法排除会员试听。
                        short = short or (not track.duration and duration <= 60)
                        prepared = PreparedPreview(data, track.provider, duration, short, track.duration > 0)
                        if short:
                            if not self.config.allow_preview_fallback:
                                raise PreviewError('返回了短试听或不完整音频，请检查登录权限；未发送')
                            fallback = fallback or prepared
                            continue
                        self._cache(path, prepared)
                        return prepared
                    except PreviewError as exc:
                        errors.append(f'{provider.name}：{exc}')
            except PreviewError as exc:
                errors.append(f'{provider.name}：{exc}')
            except (ValueError, TypeError, KeyError, AttributeError, IndexError):
                errors.append(f'{provider.name}：接口响应格式异常')
        if fallback is not None:
            self._cache(path, fallback)
            return fallback
        grouped = {}
        for error in dict.fromkeys(errors):
            name, _, reason = error.partition('：')
            grouped.setdefault(reason, []).append(name)
        summary = '；'.join('/'.join(names) + '：' + reason for reason, names in grouped.items())
        raise PreviewError(summary or '未找到完整版音源；可配置登录接口或允许试听片段兜底')

    def _cache(self, path, prepared):
        # WAV 与来源信息放在同一原子缓存中，避免并发覆盖后把片段标为完整版。
        with tempfile.NamedTemporaryFile(dir=self.cache_dir, suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
        try:
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED) as archive:
                archive.writestr('audio.wav', prepared.audio)
                archive.writestr('info.json', json.dumps({
                    'provider': prepared.provider, 'is_preview': prepared.is_preview,
                    'length_verified': prepared.length_verified}, ensure_ascii=False))
            temporary.replace(path)
            self.cleanup()
        finally:
            temporary.unlink(missing_ok=True)

    def voice(self, prepared):
        return encode_qq_voice(prepared.audio, prepared.duration, self.cache_dir,
                               executable=ffmpeg_executable(self.config.ffmpeg_path))

    def cover(self, song):
        if not song.cover_url:
            raise PreviewError("曲库未提供该歌曲的曲绘地址")
        try:
            payload = download(song.cover_url, limit=8 * 1024 * 1024,
                               timeout=self.config.request_timeout_seconds)
            with Image.open(BytesIO(payload)) as image:
                if image.width * image.height > 16000000:
                    raise PreviewError("曲绘尺寸超过限制")
                image.load()
            # 校验后原样发送，不缩放、不重新编码，也不添加文字或边框。
            return payload
        except PreviewError as exc:
            raise PreviewError(f'曲绘获取失败：{exc}') from exc
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise PreviewError('曲绘解码失败：接口返回的不是有效图片') from exc

    def cleanup(self):
        files = []
        for path in self.cache_dir.glob('*.preview'):
            try:
                files.append((path.stat().st_mtime, path.stat().st_size, path))
            except FileNotFoundError:
                continue
        now, total = time.time(), 0
        for modified, size, path in sorted(files, reverse=True):
            total += size
            if now - modified >= self.config.cache_ttl_seconds or total > 256 * 1024 * 1024:
                path.unlink(missing_ok=True)
