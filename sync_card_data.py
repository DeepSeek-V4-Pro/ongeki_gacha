#!/usr/bin/env python3
"""Sync ONGEKI card JSON/PNG data into the plugin's default data directory.

Usage:
    python sync_card_data.py --dry-run
    python sync_card_data.py
    python sync_card_data.py --check
    python sync_card_data.py --check --quick

Options:
    --source          source card PNG directory
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
DEFAULT_SOURCE_CARDS = Path(r"D:\Tools\ONGEKI_unpack\output\cards_2690")
DEFAULT_SOURCE_JSON = Path(r"D:\Tools\ONGEKI_unpack\output\card_info_merged.json")
DEFAULT_DEST = SCRIPT_DIR / "assets" / "card_data"
JSON_NAME = "card_info_merged.json"
MANIFEST_NAME = "card_data_manifest.json"


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
        card_id = row.get("id")
        name = str(
            row.get("imageFile")
            or (f"ui_card_{int(card_id):06d}.png" if card_id is not None else "")
        )
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
    json_meta = manifest.get("json") or {}
    if not quick:
        actual_json_hash = sha256(json_path)
        if json_meta.get("sha256") != actual_json_hash:
            print("FAIL: card_info_merged.json hash mismatch")
            return False
    files = manifest.get("files") or []
    checked = 0
    for item in files:
        name = str(item.get("name", ""))
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
    parser.add_argument("--source", default=str(DEFAULT_SOURCE_CARDS))
    parser.add_argument("--source-json", default=str(DEFAULT_SOURCE_JSON))
    parser.add_argument("--dest", default=str(DEFAULT_DEST))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source_cards = resolve_path(args.source, SCRIPT_DIR)
    source_json = resolve_path(args.source_json, SCRIPT_DIR)
    dest = resolve_path(args.dest, SCRIPT_DIR)

    if args.check:
        return 0 if check_dest(dest, args.quick) else 1
    return sync(source_cards, source_json, dest, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
