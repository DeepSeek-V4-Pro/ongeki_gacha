#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交互式配置曲目预览音源（MaiBot / AstrBot 通用）。

直接运行：

    python tools/configure_preview.py

脚本会自动识别当前插件平台，读取现有配置，引导填写网易云登录接口或
Meting-API 地址，测试连接后写回配置并备份原文件。也支持非交互参数，
例如：

    python tools/configure_preview.py --provider netease_api \
        --netease-api-url http://127.0.0.1:3000 --netease-cookie "MUSIC_U=..." --yes

注意：Cookie 是敏感信息，只会写入本机配置文件，不要提交到仓库。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 旧 Python 的兜底
    tomllib = None

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
IS_ASTRBOT = (PLUGIN_ROOT / "metadata.yaml").is_file()
PLATFORM = "AstrBot" if IS_ASTRBOT else "MaiBot"
ASTRBOT_CONFIG_NAME = "astrbot_plugin_ongeki_gacha_config.json"

PREVIEW_KEYS = (
    "enabled",
    "automatic_ultimate",
    "providers",
    "netease_api_url",
    "netease_cookie",
    "meting_api_url",
    "allow_preview_fallback",
    "compress_media",
    "max_duration_seconds",
    "ffmpeg_path",
    "request_timeout_seconds",
    "download_timeout_seconds",
    "total_timeout_seconds",
    "send_timeout_seconds",
    "cooldown_seconds",
    "cache_ttl_seconds",
    "max_download_bytes",
)

PREVIEW_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "automatic_ultimate": True,
    "providers": ["netease_api", "netease", "meting_tencent", "meting_kugou",
                  "meting_kuwo", "meting_netease", "deezer"],
    "netease_api_url": "",
    "netease_cookie": "",
    "meting_api_url": "",
    "allow_preview_fallback": False,
    "compress_media": True,
    "max_duration_seconds": 1200,
    "ffmpeg_path": "",
    "request_timeout_seconds": 15,
    "download_timeout_seconds": 60,
    "total_timeout_seconds": 180,
    "send_timeout_seconds": 90,
    "cooldown_seconds": 10,
    "cache_ttl_seconds": 86400,
    "max_download_bytes": 67108864,
}

SUPPORTED_PROVIDERS = {
    "netease_api": "网易云登录接口",
    "netease": "网易云公开接口",
    "meting_tencent": "Meting / QQ音乐",
    "meting_kugou": "Meting / 酷狗",
    "meting_kuwo": "Meting / 酷我",
    "meting_netease": "Meting / 网易云",
    "deezer": "Deezer 短试听",
}
METING_SERVERS = {
    "1": ("tencent", "QQ音乐"),
    "2": ("kugou", "酷狗"),
    "3": ("kuwo", "酷我"),
    "4": ("netease", "网易云"),
}
PROVIDER_PRESETS = {
    "netease_api": ["netease_api", "netease"],
    "meting": ["meting_tencent", "meting_kugou", "meting_kuwo", "meting_netease", "netease"],
    "anonymous": ["netease"],
    "deezer": ["deezer"],
}


def _load_toml(text: str) -> dict[str, Any]:
    if tomllib is None:
        return {}
    try:
        return tomllib.loads(text)
    except Exception:
        return {}


def _toml_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return _toml_string(value)


def render_preview_toml(values: dict[str, Any]) -> str:
    lines = ["[preview]"]
    for key in PREVIEW_KEYS:
        lines.append(f"{key} = {_toml_value(values[key])}")
    # 保留未来版本或手工添加的预览键，避免一键配置把它们删掉。
    for key, value in values.items():
        if key not in PREVIEW_KEYS:
            lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines) + "\n"


def _replace_preview_section(text: str, values: dict[str, Any]) -> str:
    section = render_preview_toml(values)
    pattern = re.compile(r"(?ms)^\[preview\]\s*$\n?.*?(?=^\[|\Z)")
    if pattern.search(text):
        return pattern.sub(lambda _match: section, text, count=1)
    return text.rstrip() + "\n\n" + section


def _preview_from_toml(text: str) -> dict[str, Any]:
    data = _load_toml(text)
    if isinstance(data.get("preview"), dict):
        return dict(data["preview"])
    match = re.search(r"(?ms)^\[preview\]\s*$\n?(.*?)(?=^\[|\Z)", text)
    if not match:
        return {}
    values: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        raw = raw.split("#", 1)[0].strip()
        try:
            if raw in {"true", "false"}:
                values[key.strip()] = raw == "true"
            elif raw.startswith("["):
                values[key.strip()] = json.loads(raw)
            elif raw.startswith(('"', "'")):
                values[key.strip()] = json.loads(raw) if raw.startswith('"') else raw.strip("'")
            else:
                values[key.strip()] = int(raw)
        except (ValueError, json.JSONDecodeError):
            continue
    return values


def detect_config_path(explicit: str = "") -> Path | None:
    if explicit:
        return Path(explicit).expanduser().resolve()
    if not IS_ASTRBOT:
        return PLUGIN_ROOT / "config.toml"
    candidates: list[Path] = []
    for variable in ("ASTRBOT_ROOT", "ASTRBOT_DATA"):
        raw = os.environ.get(variable, "").strip()
        if raw:
            root = Path(raw).expanduser()
            candidates.extend([root / "data" / "config", root / "config"])
    candidates.append(Path.home() / ".astrbot" / "data" / "config")
    for parent in PLUGIN_ROOT.parents:
        candidates.extend([parent / "config", parent / "data" / "config"])
    for folder in candidates:
        path = folder / ASTRBOT_CONFIG_NAME
        if path.is_file():
            return path
    return None


def read_preview(path: Path) -> dict[str, Any]:
    values = dict(PREVIEW_DEFAULTS)
    if not path.is_file():
        return values
    try:
        text = path.read_text(encoding="utf-8-sig")
        if path.suffix.lower() == ".json":
            data = json.loads(text)
            if isinstance(data.get("preview"), dict):
                values.update(data["preview"])
        else:
            values.update(_preview_from_toml(text))
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    values["providers"] = list(values.get("providers") or PREVIEW_DEFAULTS["providers"])
    return values


def validate_values(values: dict[str, Any]) -> None:
    providers = values.get("providers") or []
    if not isinstance(providers, list) or not providers:
        raise ValueError("至少保留一个音源")
    if len(set(providers)) != len(providers):
        raise ValueError("音源不可重复")
    unknown = [name for name in providers if name not in SUPPORTED_PROVIDERS]
    if unknown:
        raise ValueError("不支持的音源：" + "、".join(unknown))
    for key in ("netease_api_url", "meting_api_url"):
        address = str(values.get(key) or "").strip()
        if not address:
            continue
        parsed = urllib.parse.urlsplit(address)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"{key} 需要完整的 http(s) 地址")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError(f"{key} 不能包含账号密码或 # 片段")
    limits = {
        "max_duration_seconds": (60, 3600),
        "request_timeout_seconds": (3, 60),
        "download_timeout_seconds": (5, 180),
        "total_timeout_seconds": (30, 600),
        "send_timeout_seconds": (5, 180),
        "cooldown_seconds": (0, 60),
        "cache_ttl_seconds": (60, 7 * 24 * 3600),
        "max_download_bytes": (1024, 128 * 1024 * 1024),
    }
    for key, (minimum, maximum) in limits.items():
        value = int(values.get(key, 0))
        if not minimum <= value <= maximum:
            raise ValueError(f"{key} 应在 {minimum}～{maximum} 之间")


def write_config(path: Path, values: dict[str, Any]) -> None:
    validate_values(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_name(path.name + f".bak-{stamp}"))
    if path.suffix.lower() == ".json":
        data: dict[str, Any] = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, ValueError, json.JSONDecodeError):
                data = {}
        preview = data.get("preview") if isinstance(data.get("preview"), dict) else {}
        preview.update({key: values[key] for key in PREVIEW_KEYS})
        data["preview"] = preview
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    else:
        original = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
        existing = _preview_from_toml(original) if original else {}
        text = _replace_preview_section(original, {**existing, **values})
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def mask_secret(value: str) -> str:
    value = str(value or "")
    if not value:
        return "未设置"
    if len(value) <= 12:
        return "已设置"
    return value[:6] + "…" + value[-4:]


def _http_json(url: str, *, form: dict[str, str] | None = None, timeout: int = 12) -> Any:
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    request = urllib.request.Request(
        url, data=data, headers={"User-Agent": "OngekiGachaPreviewSetup/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(4 * 1024 * 1024)
    return json.loads(raw.decode("utf-8", "replace"))


def _short_results(rows: list[dict[str, Any]], title_key: str, artist_key: str) -> str:
    items = []
    for row in rows[:3]:
        if not isinstance(row, dict):
            continue
        title = str(row.get(title_key) or "").strip()
        artist = row.get(artist_key)
        if isinstance(artist, dict):
            artist = artist.get("name", "")
        if isinstance(artist, list):
            artist = "/".join(str(item.get("name", "")) if isinstance(item, dict) else str(item)
                              for item in artist)
        items.append(f"{title} — {artist}")
    return "；".join(items) or "接口返回空列表"


def test_provider(name: str, values: dict[str, Any]) -> str:
    if name == "netease_api":
        base = str(values.get("netease_api_url") or "").rstrip("/")
        if not base:
            return "跳过：未配置接口地址"
        form = {"keywords": "怨撃 細江慎治", "type": "1", "limit": "3", "unblock": "false"}
        if values.get("netease_cookie"):
            form["cookie"] = str(values["netease_cookie"])
        data = _http_json(base + "/cloudsearch", form=form)
        if data.get("code") != 200:
            return f"失败：接口返回 code={data.get('code')}"
        rows = (data.get("result") or {}).get("songs") or []
        return "成功：" + _short_results(rows, "name", "ar")
    if name == "netease":
        url = "https://music.163.com/api/search/get/?" + urllib.parse.urlencode(
            {"s": "怨撃 細江慎治", "type": 1, "limit": 3, "offset": 0})
        data = _http_json(url)
        if data.get("code") != 200:
            return f"失败：接口返回 code={data.get('code')}"
        rows = (data.get("result") or {}).get("songs") or []
        return "成功：" + _short_results(rows, "name", "artists")
    if name.startswith("meting_"):
        base = str(values.get("meting_api_url") or "").strip()
        if not base:
            return "跳过：未配置 Meting 端点"
        server = name.removeprefix("meting_")
        parsed = urllib.parse.urlsplit(base)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        query.update(server=server, type="search", id="怨撃 細江慎治")
        url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
        data = _http_json(url)
        if not isinstance(data, list):
            return "失败：接口没有返回列表"
        return "成功：" + _short_results(data, "title", "author")
    if name == "deezer":
        url = "https://api.deezer.com/search?" + urllib.parse.urlencode(
            {"q": "怨撃 細江慎治", "limit": 3})
        data = _http_json(url)
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return "失败：接口没有返回歌曲列表"
        return "成功：" + _short_results(rows, "title", "artist")
    return "未知音源"


def run_tests(values: dict[str, Any]) -> None:
    print("\n开始测试已启用的音源（只验证搜索，不下载音频）…")
    for name in values.get("providers") or []:
        label = SUPPORTED_PROVIDERS.get(name, name)
        if name == "deezer" and not values.get("allow_preview_fallback"):
            print(f"- {label}: 跳过（未开启短试听兜底）")
            continue
        try:
            result = test_provider(name, values)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            result = f"失败：{type(exc).__name__}: {exc}"
        print(f"- {label}: {result}")
    executable = find_ffmpeg(values.get("ffmpeg_path", ""))
    if executable:
        print(f"- FFmpeg: 找到 {executable}")
    else:
        print("- FFmpeg: 未找到（语音转换会失败，请安装 imageio-ffmpeg 或填写路径）")


def find_ffmpeg(configured: str = "") -> str:
    configured = str(configured or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.is_file() else ""
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in (Path.home() / "ffmpeg" / "bin" / "ffmpeg.exe",):
        if candidate.is_file():
            return str(candidate)
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return ""


def ffmpeg_version(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "-version"], capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return "无法运行"
    if result.returncode:
        return "运行失败"
    first = (result.stdout or result.stderr or "").splitlines()
    return first[0][:80] if first else "可用"


def _print_current(values: dict[str, Any], path: Path | None) -> None:
    print("=" * 62)
    print(f"音击抽卡模拟器 · 曲目预览音源配置（{PLATFORM}）")
    print(f"配置文件：{path or '未找到，请用 --config 指定'}")
    print(f"音源顺序：{'、'.join(values.get('providers') or [])}")
    print(f"网易云登录接口：{values.get('netease_api_url') or '未配置'}")
    print(f"网易云 Cookie：{mask_secret(values.get('netease_cookie', ''))}")
    print(f"Meting 端点：{values.get('meting_api_url') or '未配置'}")
    print(f"短试听兜底：{'开启' if values.get('allow_preview_fallback') else '关闭'}")
    print(f"压缩曲绘与语音：{'开启' if values.get('compress_media', True) else '关闭'}")
    print(f"FFmpeg：{values.get('ffmpeg_path') or '自动查找'}")
    print("=" * 62)


def _prompt(label: str, default: str = "", *, secret: bool = False) -> str:
    suffix = f"（当前 {default}）" if default and not secret else ""
    if secret:
        suffix = "（已设置，回车保留，输入 - 清除）" if default else "（可留空）"
    try:
        raw = input(f"{label}{suffix}: ").strip()
    except EOFError:
        return default
    if not raw:
        return default
    if secret and raw == "-":
        return ""
    return raw


def _prompt_int(label: str, default: int, minimum: int, maximum: int) -> int:
    while True:
        raw = _prompt(f"{label} [{minimum}～{maximum}]", str(default))
        try:
            value = int(raw)
        except ValueError:
            print("请输入整数。")
            continue
        if minimum <= value <= maximum:
            return value
        print(f"请输入 {minimum}～{maximum} 之间的整数。")


def _prompt_bool(label: str, default: bool) -> bool:
    current = "Y/n" if default else "y/N"
    raw = _prompt(f"{label} [{current}]", "").lower()
    if not raw:
        return default
    return raw in {"y", "yes", "是", "1", "开", "开启"}


def _add_providers(values: dict[str, Any], names: list[str]) -> None:
    ordered = []
    for name in names + list(values.get("providers") or []):
        if name in SUPPORTED_PROVIDERS and name not in ordered:
            ordered.append(name)
    values["providers"] = ordered


def interactive(values: dict[str, Any], path: Path | None) -> dict[str, Any]:
    while True:
        _print_current(values, path)
        print("1) 接入网易云登录接口（推荐，可拿完整音频）")
        print("2) 接入 Meting-API（QQ音乐 / 酷狗 / 酷我 / 网易云）")
        print("3) 仅使用匿名网易云公开接口")
        print("4) 调整试听参数与 FFmpeg")
        print("5) 自定义音源顺序")
        print("6) 测试已启用音源")
        print("0) 保存并退出")
        try:
            choice = input("请选择: ").strip()
        except EOFError:
            return values
        if choice == "0":
            return values
        if choice == "1":
            url = _prompt("接口地址，例如 http://127.0.0.1:3000",
                          str(values.get("netease_api_url") or "http://127.0.0.1:3000"))
            cookie = _prompt("网易云 Cookie", str(values.get("netease_cookie") or ""), secret=True)
            values["netease_api_url"] = url
            values["netease_cookie"] = cookie
            _add_providers(values, ["netease_api", "netease"])
        elif choice == "2":
            url = _prompt("Meting 完整端点，例如 http://127.0.0.1:3001/api",
                          str(values.get("meting_api_url") or "http://127.0.0.1:3001/api"))
            print("平台：1 QQ音乐  2 酷狗  3 酷我  4 网易云  5 全部")
            platform = input("请选择 [5]: ").strip() or "5"
            values["meting_api_url"] = url
            if platform == "5":
                _add_providers(values, [f"meting_{server}" for server, _ in METING_SERVERS.values()])
            elif platform in METING_SERVERS:
                _add_providers(values, ["meting_" + METING_SERVERS[platform][0]])
            else:
                print("未识别的平台，保留原音源顺序。")
        elif choice == "3":
            values["providers"] = ["netease"] + (
                ["deezer"] if values.get("allow_preview_fallback") else [])
        elif choice == "4":
            values["enabled"] = _prompt_bool("启用曲目预览", bool(values.get("enabled", True)))
            values["automatic_ultimate"] = _prompt_bool(
                "终极任务接取后自动试听", bool(values.get("automatic_ultimate", True)))
            values["allow_preview_fallback"] = _prompt_bool(
                "允许短试听兜底", bool(values.get("allow_preview_fallback", False)))
            values["compress_media"] = _prompt_bool(
                "压缩预览曲绘与语音", bool(values.get("compress_media", True)))
            values["max_duration_seconds"] = _prompt_int(
                "最长音频秒数", int(values["max_duration_seconds"]), 60, 3600)
            values["request_timeout_seconds"] = _prompt_int(
                "元数据请求超时", int(values["request_timeout_seconds"]), 3, 60)
            values["download_timeout_seconds"] = _prompt_int(
                "音频下载超时", int(values["download_timeout_seconds"]), 5, 180)
            values["total_timeout_seconds"] = _prompt_int(
                "音源总预算", int(values["total_timeout_seconds"]), 30, 600)
            values["send_timeout_seconds"] = _prompt_int(
                "发送超时", int(values["send_timeout_seconds"]), 5, 180)
            values["cooldown_seconds"] = _prompt_int(
                "同账号冷却", int(values["cooldown_seconds"]), 0, 60)
            executable = find_ffmpeg(values.get("ffmpeg_path", ""))
            default_ffmpeg = str(values.get("ffmpeg_path") or executable)
            values["ffmpeg_path"] = _prompt("FFmpeg 路径（留空自动查找）", default_ffmpeg)
        elif choice == "5":
            print("可用音源：" + "、".join(SUPPORTED_PROVIDERS))
            raw = _prompt("按顺序填写，用逗号分隔", "、".join(values.get("providers") or []))
            names = [item.strip() for item in re.split(r"[,，、\s]+", raw) if item.strip()]
            try:
                validate_values({**values, "providers": names})
            except ValueError as exc:
                print(f"顺序无效：{exc}")
            else:
                values["providers"] = names
        elif choice == "6":
            run_tests(values)
        else:
            print("请输入 0～6。")


def _apply_provider_preset(values: dict[str, Any], provider: str) -> None:
    if provider == "custom":
        return
    values["providers"] = list(PROVIDER_PRESETS[provider])
    if provider == "deezer":
        values["allow_preview_fallback"] = True


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="交互式配置音击抽卡模拟器的曲目预览音源")
    parser.add_argument("--config", default="", help="配置文件路径（默认自动查找）")
    parser.add_argument("--provider", choices=[*PROVIDER_PRESETS, "custom"],
                        help="非交互模式：选择音源预设")
    parser.add_argument("--providers", default="", help="非交互模式：逗号分隔的音源顺序")
    parser.add_argument("--netease-api-url", default=None)
    parser.add_argument("--netease-cookie", default=None)
    parser.add_argument("--meting-api-url", default=None)
    parser.add_argument("--ffmpeg", default=None)
    parser.add_argument("--allow-preview-fallback", dest="fallback", action="store_true", default=None)
    parser.add_argument("--no-preview-fallback", dest="fallback", action="store_false")
    parser.add_argument("--compress-media", dest="compress", action="store_true", default=None)
    parser.add_argument("--no-compress-media", dest="compress", action="store_false")
    parser.add_argument("--max-duration", type=int, default=None)
    parser.add_argument("--test", action="store_true", help="写完或读取后测试音源")
    parser.add_argument("--yes", action="store_true", help="非交互模式：不询问，直接写入")
    parser.add_argument("--dry-run", action="store_true", help="只显示将写入的配置，不落盘")
    return parser.parse_args(argv)


def _apply_overrides(values: dict[str, Any], args: argparse.Namespace) -> None:
    if args.provider:
        _apply_provider_preset(values, args.provider)
    if args.providers:
        values["providers"] = [item.strip() for item in re.split(r"[,，、\s]+", args.providers) if item.strip()]
    if args.netease_api_url is not None:
        values["netease_api_url"] = args.netease_api_url.strip()
    if args.netease_cookie is not None:
        values["netease_cookie"] = args.netease_cookie
    if args.meting_api_url is not None:
        values["meting_api_url"] = args.meting_api_url.strip()
    if args.ffmpeg is not None:
        values["ffmpeg_path"] = args.ffmpeg.strip()
    if args.fallback is not None:
        values["allow_preview_fallback"] = args.fallback
    if args.compress is not None:
        values["compress_media"] = args.compress
    if args.max_duration is not None:
        values["max_duration_seconds"] = args.max_duration


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    path = detect_config_path(args.config)
    if path is None:
        print("没有找到 AstrBot 的插件配置文件。")
        print("请用 --config 指定，例如：")
        example = Path.home() / ".astrbot" / "data" / "config" / ASTRBOT_CONFIG_NAME
        print(f"  python tools/configure_preview.py --config {example}")
        return 2
    values = read_preview(path)
    _apply_overrides(values, args)
    try:
        validate_values(values)
    except ValueError as exc:
        print(f"配置无效：{exc}")
        return 2

    if args.dry_run:
        values = interactive(values, path) if (not args.yes and sys.stdin.isatty()) else values
        print(render_preview_toml(values) if path.suffix.lower() != ".json"
              else json.dumps({"preview": {key: values[key] for key in PREVIEW_KEYS}},
                              ensure_ascii=False, indent=2))
        return 0

    if not args.yes:
        if not sys.stdin.isatty():
            print("非交互环境请加 --yes 才会写入配置。")
            return 2
        values = interactive(values, path)

    try:
        validate_values(values)
    except ValueError as exc:
        print(f"配置无效：{exc}")
        return 2

    try:
        write_config(path, values)
    except OSError as exc:
        print(f"写入失败：{exc}")
        return 1
    print(f"\n已写入 {path}")
    print(f"音源顺序：{'、'.join(values['providers'])}")
    if args.test:
        run_tests(values)
    print("\n下一步：重载插件（AstrBot 可在 WebUI 重载，MaiBot 重新加载插件），")
    if IS_ASTRBOT:
        print("AstrBot 若正打开 WebUI 配置页，请先刷新或关闭，避免旧值覆盖本次写入。")
    print("然后发送 /曲目预览 音击 怨撃 验证；若仍失败，先看插件日志中的音源错误。")
    if values.get("netease_cookie"):
        print("提示：Cookie 已写入本机配置，请勿把配置文件提交到公开仓库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
