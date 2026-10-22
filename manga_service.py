"""Live official ONGEKI one-panel manga client."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import threading
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image


SOURCE_URL = "https://info-ongeki.sega.jp/wp-json/thistheme/v2/ongeki_comic"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MAX_IMAGE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class MangaEntry:
    id: str
    name: str
    title: str
    source_url: str


class MangaCatalog:
    """Fetch the live official manga list with a short in-memory cache."""

    def __init__(self, *, cache_ttl_seconds: int = 300) -> None:
        self.cache_ttl_seconds = max(int(cache_ttl_seconds), 0)
        self.entries: tuple[MangaEntry, ...] = ()
        self.by_id: dict[str, MangaEntry] = {}
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _first(value: object) -> str:
        if isinstance(value, list):
            return str(value[0]).strip() if value else ""
        return str(value or "").strip()

    @staticmethod
    def _official_image_url(value: object) -> str:
        url = str(value or "").strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "info-ongeki.sega.jp":
            raise ValueError("漫画图片不是官方域名")
        return url

    def refresh(self, *, force: bool = False) -> tuple[MangaEntry, ...]:
        with self._lock:
            now = time.monotonic()
            if (
                not force
                and self.entries
                and now - self._loaded_at < self.cache_ttl_seconds
            ):
                return self.entries
            request = Request(
                SOURCE_URL,
                headers={
                    "User-Agent": USER_AGENT,
                    "Referer": "https://ongeki.sega.jp/manga/",
                },
            )
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, list):
                raise ValueError("漫画接口返回格式异常")
            entries: list[MangaEntry] = []
            seen: set[str] = set()
            for raw in payload:
                if not isinstance(raw, dict):
                    continue
                try:
                    source_url = self._official_image_url(raw.get("id"))
                except ValueError:
                    continue
                manga_id = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:20]
                if manga_id in seen:
                    continue
                seen.add(manga_id)
                entries.append(
                    MangaEntry(
                        id=manga_id,
                        name=self._first(raw.get("name")),
                        title=self._first(raw.get("title")),
                        source_url=source_url,
                    )
                )
            if not entries:
                raise ValueError("漫画接口没有有效条目")
            self.entries = tuple(entries)
            self.by_id = {entry.id: entry for entry in entries}
            self._loaded_at = now
            return self.entries

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(entry.id for entry in self.entries)

    def download(self, entry: MangaEntry) -> bytes:
        request = Request(
            entry.source_url,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://ongeki.sega.jp/manga/",
            },
        )
        with urlopen(request, timeout=45) as response:
            final = urlparse(response.geturl())
            if final.scheme != "https" or final.hostname != "info-ongeki.sega.jp":
                raise ValueError("漫画图片重定向到了非官方域名")
            length = int(response.headers.get("Content-Length") or 0)
            if length > MAX_IMAGE_BYTES:
                raise ValueError("漫画图片超过发送体积上限")
            data = response.read(MAX_IMAGE_BYTES + 1)
        if not data:
            raise ValueError("漫画图片为空")
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("漫画图片超过发送体积上限")
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        return data
