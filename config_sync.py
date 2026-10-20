"""插件升级后补齐配置文件：保留用户已有值，只补缺失键并迁移旧写法。"""
from __future__ import annotations

import json
import re
import shutil
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SECTION_RE = re.compile(r"^\s*\[(?P<name>[^\[\]]+)\]\s*(?:#.*)?$")
_KEY_RE = re.compile(r"^\s*(?P<key>[A-Za-z0-9_\-]+)\s*=")
_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0"}


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        pairs = ", ".join(f"{key} = {_format_value(item)}" for key, item in value.items())
        return "{ " + pairs + " }"
    return json.dumps(str(value), ensure_ascii=False)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _backup(path: Path) -> Path:
    target = path.with_name(f"{path.name}.bak-{_stamp()}")
    shutil.copy2(path, target)
    return target


def _section_bounds(lines: list[str]) -> dict[str, tuple[int, int]]:
    """返回每个 [section] 的 (表头行号, 最后一条内容行号)。"""
    bounds: dict[str, tuple[int, int]] = {}
    current: str | None = None
    for index, line in enumerate(lines):
        match = _SECTION_RE.match(line)
        if match:
            current = match.group("name").strip()
            bounds[current] = (index, index)
            continue
        if not line.strip():
            continue
        if current is None:
            continue
        start, _ = bounds[current]
        bounds[current] = (start, index)
    return bounds


def migrate_values(raw: dict[str, Any]) -> dict[tuple[str, str], Any]:
    """把旧版本的默认写法升级为新默认值，返回需要就地改写的键值。

    只迁移「整组仍是旧默认值」的情况：用户把其中任何一项改成别的值，
    就视为有意保留自定义配置，不再覆盖。
    """
    overrides: dict[tuple[str, str], Any] = {}
    preview = raw.get("preview")
    if isinstance(preview, dict) and isinstance(preview.get("compress_media"), bool):
        overrides[("preview", "compress_media")] = "full" if preview["compress_media"] else "none"
    pool = raw.get("pool")
    if isinstance(pool, dict):
        try:
            interval = int(pool.get("rotation_interval_days") or 0)
        except (TypeError, ValueError):
            interval = 0
        if str(pool.get("rotation_mode") or "").strip().lower() == "cycle" and interval == 15:
            overrides[("pool", "rotation_mode")] = "official"
            overrides[("pool", "rotation_interval_days")] = 30
    economy = raw.get("economy")
    if isinstance(economy, dict):
        try:
            days = int(economy.get("streak_cycle_days") or 0)
            reward = int(economy.get("streak_cycle_reward") or 0)
        except (TypeError, ValueError):
            days = reward = 0
        if days == 15 and reward == 1200:
            overrides[("economy", "streak_cycle_days")] = 30
            overrides[("economy", "streak_cycle_reward")] = 2400
    return overrides


def _describe(key: tuple[str, str], value: Any) -> str:
    if isinstance(value, bool):
        value = "true" if value else "false"
    return f"{key[0]}.{key[1]} -> {value}"


def sync_toml_config(path: Path, defaults: dict[str, Any], version: str) -> dict[str, Any]:
    """补齐 config.toml 缺的键、迁移旧写法并写入版本号；返回变更摘要。"""
    summary: dict[str, Any] = {
        "path": str(path),
        "changed": False,
        "added": [],
        "migrated": [],
        "backup": "",
        "error": "",
    }
    if not path.is_file():
        # 没有旧文件就整份生成，字段顺序与默认配置一致。
        lines: list[str] = []
        for section, values in defaults.items():
            if not isinstance(values, dict):
                continue
            lines.append(f"[{section}]")
            for key, value in values.items():
                lines.append(f"{key} = {_format_value(value)}")
            lines.append("")
        while lines and not lines[-1].strip():
            lines.pop()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        summary.update(changed=True, added=["<new file>"])
        return summary
    try:
        text = path.read_text(encoding="utf-8")
        raw = tomllib.loads(text)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        summary["error"] = f"配置文件无法解析：{type(exc).__name__}"
        return summary

    parsed = raw
    overrides = migrate_values(raw)
    migrated = [_describe(key, value) for key, value in overrides.items()]
    lines = text.splitlines()
    bounds = _section_bounds(lines)
    inserts: list[tuple[int, list[str]]] = []
    appended: list[str] = []
    added: list[str] = []

    for section, values in defaults.items():
        if not isinstance(values, dict):
            continue
        current = parsed.get(section)
        existing = current if isinstance(current, dict) else {}
        missing = [key for key in values if key not in existing]
        if not missing:
            continue
        new_lines = [f"{key} = {_format_value(values[key])}" for key in missing]
        added.extend(f"{section}.{key}" for key in missing)
        if section in bounds:
            inserts.append((bounds[section][1] + 1, new_lines))
        else:
            appended.extend(["", f"[{section}]", *new_lines])

    # 版本号与旧默认值就地改写。
    rewrites: dict[int, str] = {}
    targets = [("plugin", "config_version", version)]
    targets.extend((section, key, value) for (section, key), value in overrides.items())
    for section, key, value in targets:
        span = bounds.get(section)
        if span is None:
            continue
        desired = f"{key} = {_format_value(value)}"
        for index in range(span[0], span[1] + 1):
            match = _KEY_RE.match(lines[index])
            if match and match.group("key") == key:
                if lines[index].strip() != desired:
                    rewrites[index] = desired
                break

    if not added and not rewrites:
        return summary

    for index, value in rewrites.items():
        lines[index] = value
    for position, new_lines in sorted(inserts, key=lambda item: item[0], reverse=True):
        lines[position:position] = new_lines
    lines.extend(appended)

    backup = _backup(path)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary.update(changed=True, added=added, migrated=migrated, backup=str(backup))
    return summary


def sync_json_config(path: Path, defaults: dict[str, Any], version: str) -> dict[str, Any]:
    """AstrBot 侧：补齐 JSON 配置缺的键，同样保留用户已有值。"""
    summary: dict[str, Any] = {
        "path": str(path),
        "changed": False,
        "added": [],
        "migrated": [],
        "backup": "",
        "error": "",
    }
    if not path.is_file():
        summary["error"] = "配置文件不存在"
        return summary
    try:
        raw_bytes = path.read_bytes()
        # AstrBot 写出的 JSON 可能带 UTF-8 BOM，读的时候按 utf-8-sig 解，写回时保留。
        has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
        raw = json.loads(raw_bytes.decode("utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        summary["error"] = f"配置文件无法解析：{type(exc).__name__}"
        return summary
    if not isinstance(raw, dict):
        summary["error"] = "配置文件不是 JSON 对象"
        return summary

    added: list[str] = []

    def merge(target: dict[str, Any], source: dict[str, Any], prefix: str) -> None:
        for key, value in source.items():
            if key not in target:
                target[key] = value
                added.append(f"{prefix}{key}")
            elif isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value, f"{prefix}{key}.")

    merge(raw, defaults, "")
    migrated: list[str] = []
    for (section, key), value in migrate_values(raw).items():
        current = raw.get(section)
        if isinstance(current, dict) and current.get(key) != value:
            current[key] = value
            migrated.append(_describe((section, key), value))
    plugin = raw.get("plugin")
    if not isinstance(plugin, dict):
        plugin = raw.setdefault("plugin", {})
    if plugin.get("config_version") != version:
        plugin["config_version"] = version
        added.append("plugin.config_version")

    if not added and not migrated:
        return summary
    backup = _backup(path)
    text = json.dumps(raw, ensure_ascii=False, indent=2) + "\n"
    path.write_text(("\ufeff" if has_bom else "") + text, encoding="utf-8")
    summary.update(changed=True, added=added, migrated=migrated, backup=str(backup))
    return summary
