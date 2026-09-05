#!/usr/bin/env python3
"""Sync ONGEKI card JSON/PNG data into the plugin's default data directory.

Usage:
    python sync_card_data.py --dry-run
    python sync_card_data.py
    python sync_card_data.py --check
    python sync_card_data.py --check --quick

Options:
    --source          source card PNG directory
                      (default: plugin assets/card_data)
    --source-json     source card_info_merged.json
    --dest            destination directory (default: assets/card_data)
    --check           verify an existing data directory
    --quick           check file existence only, skip SHA-256
    --dry-run         print planned actions without writing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE_CARDS = SCRIPT_DIR / "assets" / "card_data"
DEFAULT_SOURCE_JSON = SCRIPT_DIR / "assets" / "card_data" / "card_info_merged.json"
DEFAULT_DEST = SCRIPT_DIR / "assets" / "card_data"
JSON_NAME = "card_info_merged.json"
MANIFEST_NAME = "card_data_manifest.json"
POOL_NAME = "gacha_pools.json"


def resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def image_names(json_path: Path) -> list[str]:
    rows = json.loads(json_path.read_text(encoding="utf-8-sig"))
    names: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not bool(row.get("imagePresent", False)):
            continue
        card_id = row.get("id")
        if card_id is None:
            continue
        try:
            normalized_id = int(card_id)
        except (TypeError, ValueError):
            continue
        name = str(row.get("imageFile") or f"ui_card_{normalized_id:06d}.png")
        if name:
            names.append(name)
    return sorted(set(names))


def check_dest(dest: Path, quick: bool) -> bool:
    json_path = dest / JSON_NAME
    manifest_path = dest / MANIFEST_NAME
    if not json_path.is_file():
        print(f"FAIL: missing {JSON_NAME}")
        return False
    if not manifest_path.is_file():
        print(f"WARN: {MANIFEST_NAME} missing; performing existence-only check")
        names = image_names(json_path)
        missing = [name for name in names if not (dest / name).is_file()]
        if missing:
            print(f"FAIL: {len(missing)} image files missing, e.g. {missing[:5]}")
            return False
        print(f"OK: {len(names)} images exist (no hash manifest)")
        return True

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        print("FAIL: card_data_manifest.json 顶层必须是对象")
        return False
    json_meta = manifest.get("json") or {}
    if not isinstance(json_meta, dict) or json_meta.get("name") != JSON_NAME:
        print("FAIL: card_data_manifest.json 缺少或未登记 card_info_merged.json")
        return False
    if not quick:
        actual_json_hash = sha256(json_path)
        if json_meta.get("sha256") != actual_json_hash:
            print("FAIL: card_info_merged.json hash mismatch")
            return False
    files = manifest.get("files") or []
    if not isinstance(files, list):
        print("FAIL: card_data_manifest.json 的 files 不是数组")
        return False
    expected_names = set(image_names(json_path))
    if not expected_names:
        print("FAIL: card_info_merged.json 中没有可校验的卡面记录")
        return False
    manifest_by_name: dict[str, dict] = {}
    duplicate_names: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            print("FAIL: manifest 中存在非对象文件记录")
            return False
        name = str(item.get("name") or "")
        if not name:
            print("FAIL: manifest 中存在空文件记录名")
            return False
        if name in manifest_by_name:
            duplicate_names.append(name)
        manifest_by_name[name] = item
    if duplicate_names:
        print(f"FAIL: manifest 存在重复文件名，例如 {duplicate_names[:5]}")
        return False
    missing_from_manifest = sorted(expected_names - set(manifest_by_name))
    extra_in_manifest = sorted(set(manifest_by_name) - expected_names)
    if missing_from_manifest:
        print(f"FAIL: manifest 缺少 {len(missing_from_manifest)} 个卡面记录，例如 {missing_from_manifest[:5]}")
        return False
    if extra_in_manifest:
        print(f"FAIL: manifest 包含 {len(extra_in_manifest)} 个卡表外的文件，例如 {extra_in_manifest[:5]}")
        return False
    try:
        manifest_count = int(manifest.get("count") or -1)
    except (TypeError, ValueError):
        manifest_count = -1
    if len(files) != len(expected_names) or manifest_count != len(files):
        print("FAIL: manifest count 与 files/卡表数量不一致")
        return False
    pool_meta = manifest.get("gacha_pool") or {}
    pool_path = dest / POOL_NAME
    if pool_meta and not pool_path.is_file():
        print(f"FAIL: missing {POOL_NAME}")
        return False
    if not pool_path.is_file():
        print(f"FAIL: missing {POOL_NAME}")
        return False
    if not pool_meta and pool_path.is_file():
        print(f"FAIL: {POOL_NAME} 存在，但 {MANIFEST_NAME} 未登记其哈希")
        return False
    if pool_meta and (not isinstance(pool_meta, dict) or pool_meta.get("name") != POOL_NAME):
        print(f"FAIL: {MANIFEST_NAME} 未正确登记 {POOL_NAME}")
        return False
    if pool_meta and not quick:
        actual_pool_hash = sha256(pool_path)
        if pool_meta.get("sha256") != actual_pool_hash:
            print(f"FAIL: {POOL_NAME} hash mismatch")
            return False
    checked = 0
    for name in sorted(expected_names):
        item = manifest_by_name[name]
        target = dest / name
        if not target.is_file():
            print(f"FAIL: missing {name}")
            return False
        if not quick:
            if target.stat().st_size != int(item.get("size", -1)):
                print(f"FAIL: size mismatch {name}")
                return False
            if sha256(target) != item.get("sha256"):
                print(f"FAIL: hash mismatch {name}")
                return False
        checked += 1
        if checked % 500 == 0:
            print(f"checked {checked}/{len(files)}")
    print(f"OK: {checked} files verified")
    return True


def sync(
    source_cards: Path,
    source_json: Path,
    dest: Path,
    *,
    dry_run: bool,
) -> int:
    if not source_cards.is_dir():
        print(f"FAIL: source cards directory missing: {source_cards}")
        return 1
    if not source_json.is_file():
        print(f"FAIL: source JSON missing: {source_json}")
        return 1

    names = image_names(source_json)
    if not names:
        print("FAIL: source JSON contains no card images")
        return 1

    dest_json = dest / JSON_NAME
    if dry_run:
        print(f"would sync {len(names)} images to {dest}")
        if source_json.resolve() != dest_json.resolve():
            print(f"would copy {source_json} -> {dest_json}")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    if source_json.resolve() != dest_json.resolve():
        shutil.copy2(source_json, dest_json)
        print(f"copied {source_json} -> {dest_json}")

    errors: list[str] = []
    copied = 0
    for name in names:
        source = source_cards / name
        target = dest / name
        if not source.is_file():
            errors.append(f"source missing: {source}")
            continue
        if source.resolve() == target.resolve():
            continue
        shutil.copy2(source, target)
        copied += 1
        if copied % 500 == 0:
            print(f"copied {copied}/{len(names)}")

    if errors:
        print(f"FAIL: {len(errors)} source files missing")
        for error in errors[:20]:
            print(f"  {error}")
        return 1

    json_hash = sha256(dest_json)
    files = []
    for name in names:
        target = dest / name
        files.append(
            {
                "name": name,
                "size": target.stat().st_size,
                "sha256": sha256(target),
            }
        )
        if len(files) % 500 == 0:
            print(f"hashed {len(files)}/{len(names)}")
    manifest = {
        "count": len(files),
        "json": {
            "name": JSON_NAME,
            "size": dest_json.stat().st_size,
            "sha256": json_hash,
        },
        "gacha_pool": (
            {
                "name": POOL_NAME,
                "size": (dest / POOL_NAME).stat().st_size,
                "sha256": sha256(dest / POOL_NAME),
            }
            if (dest / POOL_NAME).is_file()
            else {}
        ),
        "files": files,
    }
    (dest / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"OK: copied {copied} images and wrote {MANIFEST_NAME}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE_CARDS),
        help="卡面 PNG 目录；默认使用插件自己的 assets/card_data",
    )
    parser.add_argument(
        "--source-json",
        default=str(DEFAULT_SOURCE_JSON),
        help="卡牌信息 JSON；默认使用插件自己的 assets/card_data/card_info_merged.json",
    )
    parser.add_argument("--dest", default=str(DEFAULT_DEST), help="目标数据目录")
    parser.add_argument("--check", action="store_true", help="校验已有数据目录")
    parser.add_argument("--quick", action="store_true", help="只检查文件是否存在")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划操作，不写入")
    args = parser.parse_args()

    source_cards = resolve_path(args.source, SCRIPT_DIR)
    source_json = resolve_path(args.source_json, SCRIPT_DIR)
    dest = resolve_path(args.dest, SCRIPT_DIR)

    if args.check:
        return 0 if check_dest(dest, args.quick) else 1

    if not source_cards.is_dir():
        print(
            f"FAIL: 默认/指定卡面目录不存在: {source_cards}\n"
            "插件发布包不附带受版权保护的卡面。请将已有合法卡面放入 "
            "assets/card_data/，或通过 --source 指定自己的素材目录；"
            "具体说明见 CARD_ARTWORK_SOURCES.md。",
            file=sys.stderr,
        )
        return 1
    if not source_json.is_file():
        print(
            f"FAIL: 卡牌信息文件不存在: {source_json}\n"
            "请使用 --source-json 指定已有的 card_info_merged.json。",
            file=sys.stderr,
        )
        return 1

    return sync(source_cards, source_json, dest, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
