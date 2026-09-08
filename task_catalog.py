# -*- coding: utf-8 -*-
"""抓取音击/舞萌/中二真实曲库并归一化为随机任务候选池。

数据源：
- 音击：arcade-songs data.json
- 舞萌：lxns maimai/song/list
- 中二：lxns chunithm/song/list

用法：
    python -m ongeki_gacha.task_catalog
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from .task_render import TaskCardData, render_task_card


DEFAULT_SOURCES: dict[str, str] = {
    "ongeki": "https://dp4p6x0xfi5o9.cloudfront.net/ongeki",
    "maimai": (
        "https://maimai.lxns.net/api/v0/maimai/song/list"
        "?version=25500&notes=false"
    ),
    "chunithm": (
        "https://maimai.lxns.net/api/v0/chunithm/song/list"
        "?version=23000&notes=false"
    ),
}

DEFAULT_ASSET_BASE = {
    "ongeki": "https://dp4p6x0xfi5o9.cloudfront.net/ongeki",
    "maimai": "https://assets2.lxns.net/maimai",
    "chunithm": "https://assets2.lxns.net/chunithm",
}

logger = logging.getLogger(__name__)

GAME_LABELS = {
    "ongeki": "音击",
    "maimai": "舞萌 DX",
    "chunithm": "中二节奏",
}

KIND_LABELS = {
    "normal": "普通任务",
    "challenge": "挑战任务",
    "ultimate": "终极任务",
}

DIFFICULTY_LABELS = {
    "ongeki": ("BASIC", "ADVANCED", "EXPERT", "MASTER", "LUNATIC"),
    "maimai": ("BASIC", "ADVANCED", "EXPERT", "MASTER", "REMASTER"),
    "chunithm": ("BASIC", "ADVANCED", "EXPERT", "MASTER", "ULTIMA", "WORLD'S END"),
}


def _difficulty_label(game: str, kind: str, index: int) -> str:
    labels = DIFFICULTY_LABELS.get(game, ())
    if kind == "utage":
        return "UTAGE"
    if game == "maimai" and kind == "dx":
        base = labels[index] if index < len(labels) else f"DX {index}"
        return f"{base} DX"
    if 0 <= index < len(labels):
        return labels[index]
    return str(index)


@dataclass(frozen=True)
class CatalogChart:
    """单曲内的某一张谱面（歌曲级任务只取统计值，这里保留完整线索）。"""

    index: int
    kind: str
    label: str
    level_display: str
    level_value: float
    is_special: bool = False


@dataclass(frozen=True)
class CatalogSong:
    """归一化后的单曲任务候选。"""

    game: str
    song_id: str
    title: str
    artist: str
    max_level: float
    max_level_display: str
    max_level_value: float
    disabled: bool
    locked: bool
    cover_url: str
    charts: tuple[CatalogChart, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.game}:{self.song_id}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskSelection:
    """一次接取所需的曲目与可选目标谱面。"""

    song: CatalogSong
    chart: CatalogChart | None = None

    @property
    def requirement(self) -> str:
        if self.chart is None:
            return "游玩任意难度"
        max_difficulty_index = max(
            (item.index for item in self.song.charts if not item.is_special),
            default=-1,
        )
        suffix = "" if self.chart.index >= max_difficulty_index else " 或以上"
        base = f"{self.chart.label.upper()} {self.chart.level_display}{suffix}"
        return base


def pick_random_task(
    catalog: Iterable[CatalogSong],
    kind: str,
    *,
    completed_keys: Iterable[str] = (),
    excluded_keys: Iterable[str] = (),
    game: str | None = None,
) -> TaskSelection | None:
    """从归一化曲库中随机挑选一张普通/挑战/终极任务。"""
    catalog = list(catalog)
    completed = set(completed_keys)
    excluded = set(excluded_keys)
    if game is not None:
        catalog = [song for song in catalog if song.game == game]
    if kind == "normal":
        candidates = [
            song
            for song in catalog
            if not song.disabled and not song.locked and song.key not in excluded
        ]
        if not candidates:
            return None
        return TaskSelection(song=random.SystemRandom().choice(candidates))

    if kind == "challenge":
        candidates = [
            TaskSelection(song=song, chart=chart)
            for song in catalog
            for chart in song.charts
            if not chart.is_special
            and chart.level_value >= 10.0
            and song.key not in excluded
        ]
    elif kind == "ultimate":
        candidates = [
            TaskSelection(song=song, chart=chart)
            for song in catalog
            for chart in song.charts
            if not chart.is_special
            and chart.level_value >= 14.7
            and song.key not in completed
            and song.key not in excluded
        ]
    else:
        raise ValueError(f"未知任务类型: {kind}")
    if not candidates:
        return None
    return random.SystemRandom().choice(candidates)


def _fetch_json(url: str, timeout: int = 30, retries: int = 4) -> Any:
    """拉取 JSON，带指数退避；部分数据源会临时限流或拦截。"""
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                raw = response.read()
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"请求失败: {url}: {last_error}") from last_error


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_level(level: Any) -> float:
    """把 '12'、'12+'、'14.7' 转成可比较数字；'+' 视为高于基础级。"""
    text = str(level or "").strip().lower().replace("＋", "+")
    if not text:
        return 0.0
    plus = text.endswith("+")
    number = text.rstrip("+")
    try:
        value = float(number)
    except ValueError:
        return 0.0
    return value + (0.5 if plus else 0.0)


def _ongeki_cover_url(song: dict, source_url: str) -> str:
    image_name = str(song.get("imageName") or "").strip()
    return f"{source_url.rstrip('/')}/img/cover/{image_name}" if image_name else ""


def _normalize_ongeki(songs: list[dict], source_url: str) -> list[CatalogSong]:
    result: list[CatalogSong] = []
    for song in songs:
        if not isinstance(song, dict):
            continue
        if bool(song.get("isLocked", False)):
            continue

        title = str(song.get("title") or "").strip()
        song_id = str(song.get("songId") or title or "").strip()
        if not title and not song_id:
            continue

        charts: list[CatalogChart] = []
        for index, sheet in enumerate(song.get("sheets") or []):
            if not isinstance(sheet, dict):
                continue
            kind = str(sheet.get("type") or "std").lower()
            label = _difficulty_label("ongeki", kind, index)
            level_display = str(
                sheet.get("level")
                or sheet.get("internalLevel")
                or ""
            ).strip()
            level_value = _as_float(
                sheet.get("levelValue")
                if sheet.get("levelValue") is not None
                else sheet.get("internalLevelValue")
            )
            charts.append(
                CatalogChart(
                    index=index,
                    kind=kind,
                    label=label,
                    level_display=level_display or f"Lv.{level_value:g}",
                    level_value=level_value or _parse_level(level_display),
                    is_special=kind == "lun",
                )
            )

        standard_charts = [chart for chart in charts if not chart.is_special]
        if not standard_charts:
            continue
        max_chart = max(standard_charts, key=lambda item: item.level_value)
        result.append(
            CatalogSong(
                game="ongeki",
                song_id=song_id,
                title=title,
                artist=str(song.get("artist") or "").strip(),
                max_level=_parse_level(max_chart.level_display),
                max_level_display=max_chart.level_display,
                max_level_value=max_chart.level_value,
                disabled=False,
                locked=False,
                cover_url=_ongeki_cover_url(song, source_url),
                charts=tuple(charts),
            )
        )
    return result


def _normalize_lxns(
    game: str,
    songs: list[dict],
    asset_base: str,
    *,
    exclude_special: bool = True,
) -> list[CatalogSong]:
    result: list[CatalogSong] = []
    for song in songs:
        if not isinstance(song, dict):
            continue
        title = str(song.get("title") or "").strip()
        song_id = str(song.get("id") or "").strip()
        if not title or not song_id:
            continue
        if bool(song.get("disabled", False)) or bool(song.get("locked", False)):
            continue

        raw_difficulties = song.get("difficulties") or []
        if isinstance(raw_difficulties, dict):
            chart_items: list[tuple[str, int, dict]] = []
            for kind, charts in raw_difficulties.items():
                if kind == "utage" and exclude_special:
                    continue
                if isinstance(charts, list):
                    chart_items.extend(
                        (kind, index, chart)
                        for index, chart in enumerate(charts)
                        if isinstance(chart, dict)
                    )
        elif isinstance(raw_difficulties, list):
            chart_items = [
                ("standard", index, chart)
                for index, chart in enumerate(raw_difficulties)
                if isinstance(chart, dict)
            ]
            if exclude_special:
                chart_items = [
                    (kind, index, chart)
                    for kind, index, chart in chart_items
                    if int(_as_float(chart.get("difficulty"), 0) or 0) < 5
                ]
        else:
            chart_items = []

        charts: list[CatalogChart] = []
        standard_charts: list[CatalogChart] = []
        for outer_kind, index, chart in chart_items:
            kind = str(outer_kind or chart.get("type") or "standard").lower()
            raw_index = int(_as_float(chart.get("difficulty"), index) or index)
            label = _difficulty_label(game, kind, raw_index)
            level_display = str(chart.get("level") or "").strip()
            level_value = _as_float(chart.get("level_value"))
            is_special = kind in {"utage", "worlds_end"} or (
                game == "chunithm" and raw_index >= 5
            )
            chart_model = CatalogChart(
                index=raw_index,
                kind=kind,
                label=label,
                level_display=level_display or f"Lv.{level_value:g}",
                level_value=level_value or _parse_level(level_display),
                is_special=is_special,
            )
            charts.append(chart_model)
            if not is_special:
                standard_charts.append(chart_model)

        if not standard_charts and exclude_special:
            continue
        max_chart = max(standard_charts, key=lambda item: item.level_value)
        cover_base = asset_base.rstrip("/").replace(
            "assets2.lxns.net",
            "assets.lxns.net",
        )
        cover_url = f"{cover_base}/jacket/{song_id}.png!webp"
        result.append(
            CatalogSong(
                game=game,
                song_id=song_id,
                title=title,
                artist=str(song.get("artist") or "").strip(),
                max_level=_parse_level(max_chart.level_display),
                max_level_display=max_chart.level_display,
                max_level_value=max_chart.level_value,
                disabled=bool(song.get("disabled", False)),
                locked=bool(song.get("locked", False)),
                cover_url=cover_url,
                charts=tuple(charts),
            )
        )
    return result


def load_catalog(
    sources: dict[str, str] | None = None,
    asset_bases: dict[str, str] | None = None,
) -> list[CatalogSong]:
    """拉取并合并三个真实数据源。"""
    sources = sources or DEFAULT_SOURCES
    asset_bases = asset_bases or DEFAULT_ASSET_BASE
    result: list[CatalogSong] = []

    ongeki_data = _fetch_json(f"{sources['ongeki'].rstrip('/')}/data.json")
    ongeki_songs = (ongeki_data or {}).get("songs", [])
    result.extend(_normalize_ongeki(ongeki_songs, sources["ongeki"]))

    maimai_data = _fetch_json(sources["maimai"])
    maimai_songs = (maimai_data or {}).get("songs", [])
    result.extend(
        _normalize_lxns(
            "maimai",
            maimai_songs,
            asset_bases["maimai"],
            exclude_special=True,
        )
    )

    chunithm_data = _fetch_json(sources["chunithm"])
    chunithm_songs = (chunithm_data or {}).get("songs", [])
    result.extend(
        _normalize_lxns(
            "chunithm",
            chunithm_songs,
            asset_bases["chunithm"],
            exclude_special=True,
        )
    )
    return result


def load_or_fetch_catalog(
    cache_path: Path,
    *,
    ttl: int = 3600,
    force: bool = False,
    sources: dict[str, str] | None = None,
    asset_bases: dict[str, str] | None = None,
) -> list[CatalogSong]:
    """按 TTL 使用本地缓存，失败时回退旧缓存。"""
    if cache_path.is_file() and not force:
        age = time.time() - cache_path.stat().st_mtime
        if age < ttl:
            return _catalog_from_dicts(json.loads(cache_path.read_text(encoding="utf-8")))
    try:
        catalog = load_catalog(sources=sources, asset_bases=asset_bases)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                [song.to_dict() for song in catalog],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return catalog
    except Exception:
        if cache_path.is_file():
            return _catalog_from_dicts(json.loads(cache_path.read_text(encoding="utf-8")))
        raise


def _catalog_from_dicts(items: list[dict]) -> list[CatalogSong]:
    result: list[CatalogSong] = []
    for item in items:
        charts = tuple(CatalogChart(**chart) for chart in item.get("charts", []))
        result.append(
            CatalogSong(
                game=str(item["game"]),
                song_id=str(item["song_id"]),
                title=str(item["title"]),
                artist=str(item.get("artist", "")),
                max_level=float(item.get("max_level", 0)),
                max_level_display=str(item.get("max_level_display", "")),
                max_level_value=float(item.get("max_level_value", 0)),
                disabled=bool(item.get("disabled", False)),
                locked=bool(item.get("locked", False)),
                cover_url=str(item.get("cover_url", "")),
                charts=charts,
            )
        )
    return result


def download_cover(url: str, path: Path, retries: int = 3) -> bool:
    """下载并校验图片；优先 curl，失败再回退 Python urllib。"""
    if not url:
        return False
    if _curl_path() is not None:
        if _download_cover_via_curl(url, path):
            return True
    return _download_cover_via_python(url, path, retries=retries)


def _curl_path() -> str | None:
    candidates = (
        shutil.which("curl"),
        "C:/Windows/System32/curl.exe",
        "/usr/bin/curl",
        "/opt/homebrew/bin/curl",
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def _download_cover_via_curl(url: str, path: Path) -> bool:
    curl = _curl_path()
    if curl is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".curl")
    cmd = [
        curl,
        "-sS",
        "-L",
        "--max-time",
        "30",
        "-A",
        "Mozilla/5.0",
        "-o",
        str(tmp),
        url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=35,
            check=False,
        )
        if result.returncode != 0:
            logger.warning(
                "curl 曲绘下载失败 %s: %s",
                url,
                result.stderr.decode("utf-8", errors="replace")[:300],
            )
            tmp.unlink(missing_ok=True)
            return False
        if not tmp.is_file() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            return False
        data = tmp.read_bytes()
        with Image.open(BytesIO(data)) as image:
            image.verify()
        tmp.replace(path)
        return True
    except Exception as exc:
        logger.warning("curl 曲绘下载异常 %s: %s", url, exc)
        tmp.unlink(missing_ok=True)
        return False


def _download_cover_via_python(url: str, path: Path, retries: int = 3) -> bool:
    """原有 urllib 下载路径，作为无 curl 或 curl 失败时的兜底。"""
    last_error: Exception | None = None
    data = b""
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                content_type = response.headers.get("Content-Type", "")
                data = response.read()
            with Image.open(BytesIO(data)) as image:
                image.verify()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return True
        except Exception as exc:
            last_error = exc
            logger.warning(
                "曲绘下载失败 %s (第 %d 次): %s bytes=%d ctype=%s head=%s",
                url,
                attempt + 1,
                exc,
                len(data),
                content_type if "content_type" in locals() else "?",
                data[:16].hex(),
            )
            if attempt < retries - 1:
                time.sleep(0.5 * (2 ** attempt))
    logger.error("曲绘下载失败（已重试 %d 次）: %s: %s", retries, url, last_error)
    return False


def _sample_song(
    catalog: Iterable[CatalogSong],
    game: str,
    predicate,
) -> CatalogSong | None:
    candidates = [
        song
        for song in catalog
        if song.game == game
        and not song.disabled
        and not song.locked
        and predicate(song)
    ]
    return random.SystemRandom().choice(candidates) if candidates else None


def _render_example(
    song: CatalogSong,
    kind: str,
    *,
    output_dir: Path,
    user_id: str = "123456789",
) -> Path | None:
    if song is None:
        return None
    rewards = {"normal": 20, "challenge": 30, "ultimate": 30000}
    target_chart: CatalogChart | None = None
    if kind == "challenge":
        candidates = [
            chart
            for chart in song.charts
            if not chart.is_special and chart.level_value >= 10.0
        ]
        target_chart = max(candidates, key=lambda item: item.level_value) if candidates else None
    elif kind == "ultimate":
        candidates = [
            chart
            for chart in song.charts
            if not chart.is_special and chart.level_value >= 14.7
        ]
        target_chart = max(candidates, key=lambda item: item.level_value) if candidates else None

    if target_chart is not None:
        level_text = (
            f"{target_chart.label.upper()} {target_chart.level_display}"
            f"（定数 {target_chart.level_value:.1f}）"
        )
    else:
        level_text = f"{song.max_level_display} (定数 {song.max_level_value:.1f})"
    if target_chart is not None:
        max_difficulty_index = max(
            (chart.index for chart in song.charts if not chart.is_special),
            default=-1,
        )
        suffix = "" if target_chart.index >= max_difficulty_index else " 或以上"
        difficulty_text = (
            f"{target_chart.label.upper()} {target_chart.level_display}{suffix}"
        )
        if kind == "challenge":
            requirement = f"{difficulty_text} · S 及以上"
        else:
            requirement = f"{difficulty_text} · SSS+ 评级"
    else:
        requirement = "游玩任意难度"
    safe_song_id = hashlib.sha1(song.song_id.encode("utf-8")).hexdigest()[:10]
    cover_path = output_dir / "covers" / f"{song.game}_{safe_song_id}.png"
    download_cover(song.cover_url, cover_path)
    data = TaskCardData(
        task_id=f"20260908-{kind[:3].upper()}-{safe_song_id[:6]}",
        kind=kind,
        game=song.game,
        title=song.title,
        artist=song.artist,
        level=level_text,
        requirement=requirement,
        reward=rewards[kind],
        user_id=user_id,
        note="请游玩后发送成绩截图",
        cover_path=cover_path if cover_path.is_file() else None,
    )
    output = output_dir / f"task_card_{kind}_{song.game}.png"
    render_task_card(data, output)
    return output


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    root = Path(__file__).resolve().parent.parent
    output_dir = root / "output"
    cache_path = output_dir / "task_catalog_real.json"
    report_path = output_dir / "task_catalog_report.json"
    cards_dir = output_dir / "task_cards"

    catalog = load_or_fetch_catalog(cache_path, ttl=3600, force=True)
    normal_pool = [
        song
        for song in catalog
        if not song.disabled and not song.locked
    ]
    challenge_pool = [
        song
        for song in catalog
        if song.max_level >= 10.0
    ]
    ultimate_pool = [
        song
        for song in catalog
        if song.max_level_value >= 14.7
    ]
    challenge_chart_pool = [
        (song, chart)
        for song in catalog
        for chart in song.charts
        if not chart.is_special and chart.level_value >= 10.0
    ]
    ultimate_chart_pool = [
        (song, chart)
        for song in catalog
        for chart in song.charts
        if not chart.is_special and chart.level_value >= 14.7
    ]

    by_game = {
        game: sum(1 for song in catalog if song.game == game)
        for game in ("ongeki", "maimai", "chunithm")
    }
    challenge_by_game = {
        game: sum(1 for song in challenge_pool if song.game == game)
        for game in ("ongeki", "maimai", "chunithm")
    }
    ultimate_by_game = {
        game: sum(1 for song in ultimate_pool if song.game == game)
        for game in ("ongeki", "maimai", "chunithm")
    }

    # 普通：音击示例；挑战：舞萌示例；终极：中二示例
    samples = [
        ("normal", _sample_song(catalog, "ongeki", lambda song: True)),
        ("challenge", _sample_song(catalog, "maimai", lambda song: song.max_level >= 10.0)),
        ("ultimate", _sample_song(catalog, "chunithm", lambda song: song.max_level_value >= 14.7)),
    ]
    rendered = []
    for kind, song in samples:
        path = _render_example(song, kind, output_dir=cards_dir)
        if path is not None:
            rendered.append(str(path))

    report = {
        "source": "arcade-songs / lxns",
        "total_songs": len(catalog),
        "by_game": by_game,
        "normal_pool": len(normal_pool),
        "challenge_pool": len(challenge_pool),
        "challenge_by_game": challenge_by_game,
        "challenge_chart_pool": len(challenge_chart_pool),
        "ultimate_pool": len(ultimate_pool),
        "ultimate_by_game": ultimate_by_game,
        "ultimate_chart_pool": len(ultimate_chart_pool),
        "examples": [
            _example_report(kind, song)
            for kind, song in samples
            if song is not None
        ],
        "rendered_cards": rendered,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("=== 三游戏真实曲库合并 ===")
    print(f"总曲目：{len(catalog)}")
    for game in ("ongeki", "maimai", "chunithm"):
        print(
            f"{GAME_LABELS[game]}：全量 {by_game[game]}，"
            f"10+ {challenge_by_game[game]}，"
            f"14.7+ {ultimate_by_game[game]}"
        )
    print(f"挑战候选池：{len(challenge_pool)}")
    print(f"挑战谱面池：{len(challenge_chart_pool)}")
    print(f"终极候选池：{len(ultimate_pool)}")
    print(f"终极谱面池：{len(ultimate_chart_pool)}")
    print("示例任务卡：")
    for path in rendered:
        print(f"  {path}")
    print(f"合并报告：{report_path}")


def _example_report(kind: str, song: CatalogSong) -> dict[str, Any]:
    candidates = [
        chart
        for chart in song.charts
        if not chart.is_special
        and (
            chart.level_value >= 10.0
            if kind == "challenge"
            else chart.level_value >= 14.7
        )
    ]
    target = max(candidates, key=lambda item: item.level_value, default=None)
    difficulty_text = ""
    if target is not None:
        max_difficulty_index = max(
            (chart.index for chart in song.charts if not chart.is_special),
            default=-1,
        )
        suffix = "" if target.index >= max_difficulty_index else " 或以上"
        difficulty_text = (
            f"{target.label.upper()} {target.level_display}{suffix}"
        )
    return {
        "kind": kind,
        "game": song.game,
        "song_id": song.song_id,
        "title": song.title,
        "artist": song.artist,
        "difficulty": target.label if target is not None else "",
        "difficulty_level": target.level_display if target is not None else "",
        "difficulty_value": target.level_value if target is not None else None,
        "requirement": (
            difficulty_text
            + (
                " · S 及以上"
                if kind == "challenge"
                else " · SSS+ 评级" if kind == "ultimate" else ""
            )
        ),
        "level": song.max_level_display,
        "level_value": song.max_level_value,
        "cover_url": song.cover_url,
    }


if __name__ == "__main__":
    main()
