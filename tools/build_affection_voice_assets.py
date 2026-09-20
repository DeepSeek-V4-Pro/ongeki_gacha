"""打包170条档案语音到插件资源，保留索引与散列。

运行：python -m ongeki_gacha.tools.build_affection_voice_assets --source output/character_affection/voice_extracted
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from ..growth_core import MAIN_CHARACTER_IDS


def build(source: Path, output: Path) -> int:
    source = source.resolve()
    output = output.resolve()
    catalog_path = source / "voice_catalog.json"
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    rows = data["voices"]
    expected = {(cid, number) for cid in MAIN_CHARACTER_IDS for number in range(1, 11)}
    if len(rows) != 170 or {(int(r["character_id"]), int(r["sequence"])) for r in rows} != expected:
        raise ValueError("档案语音索引必须覆盖17名角色各10条")

    files = []
    for row in rows:
        original = (source / row["path"]).resolve()
        target = (output / row["path"]).resolve()
        if not original.is_relative_to(source) or not target.is_relative_to(output):
            raise ValueError("语音路径越界")
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        if digest != row["sha256"]:
            raise ValueError(f"语音散列不符: {row['path']}")
        files.append((original, target))

    for original, target in files:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
    (output / "voice_catalog.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return len(files)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).parents[1] / "assets/growth")
    args = parser.parse_args()
    count = build(args.source, args.output)
    print(f"Packaged {count} affection voices; listening status preserved")
