"""构建好感奖励目录：合并称号/名牌/装饰/语音/卡片数据并打包奖励图片。

运行：python -m ongeki_gacha.tools.build_reward_catalog --source output/character_affection
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..growth_core import CURVE_VERSION, MAIN_CHARACTER_IDS

VERSION = "reward-catalog-v1"
EXPECTED = {"ProfileVoice": 10, "Trophy": 9, "NormalCard": 10, "NamePlate": 2, "Attachment": 1}


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def build(source: Path, destination: Path, cards_root: Path) -> dict:
    characters = _read(source / "character_affection.json")
    if len(characters) != 17 or {int(c["id"]) for c in characters} != MAIN_CHARACTER_IDS:
        raise ValueError("角色目录必须恰好包含17名主角色")
    trophies = {int(r["Name"]["id"]): r for r in _read(source / "data/trophy.json")}
    nameplates = {int(r["Name"]["id"]): r for r in _read(source / "data/nameplate.json")}
    attachments = {int(r["Name"]["id"]): r for r in _read(source / "data/attachment.json")}

    def packaged(relative: str) -> str:
        if not (destination / relative).is_file():
            raise ValueError(f"奖励图片未打包: {relative}")
        return relative

    catalog_characters = []
    counts = {kind: 0 for kind in EXPECTED}
    for character in characters:
        cid = int(character["id"])
        rewards = []
        slots: dict[int, int] = {}
        for reward in character["rewards"]:
            kind = reward["kind"]
            level = int(reward["level"])
            rid = int(reward["id"])
            slot = slots.get(level, 0)
            slots[level] = slot + 1
            entry = {
                "level": level,
                "kind": kind,
                "kind_zh": reward.get("kind_zh", ""),
                "id": reward["id"],
                "name": reward["name"],
                "reward_key": f"{cid}:{level}:{slot}",
            }
            if kind == "Trophy":
                row = trophies.get(rid)
                if row is None:
                    raise ValueError(f"称号缺失: {rid}")
                entry["description"] = row.get("Description", "")
                entry["rarity"] = row.get("TrophyRarityType", "")
            elif kind == "NamePlate":
                row = nameplates.get(rid)
                if row is None:
                    raise ValueError(f"名牌缺失: {rid}")
                entry["description"] = row.get("Description", "")
                entry["image"] = packaged(f"images/reward_plate_{rid}.png")
                entry["icon"] = packaged(f"images/reward_plate_icon_{rid}.png")
            elif kind == "Attachment":
                row = attachments.get(rid)
                if row is None:
                    raise ValueError(f"装饰缺失: {rid}")
                entry["description"] = ""
                entry["image"] = packaged(f"images/reward_attachment_{rid}.png")
            elif kind == "ProfileVoice":
                entry["profile"] = reward.get("profile", "")
                audio = destination / "voices" / str(cid) / f"{rid}.wav"
                if not audio.is_file():
                    raise ValueError(f"语音缺失: {audio}")
                entry["audio"] = f"voices/{cid}/{rid}.wav"
            elif kind == "NormalCard":
                card_image = cards_root / f"ui_card_{rid:06d}.png"
                if not card_image.is_file():
                    raise ValueError(f"卡面缺失: {card_image}")
                entry["card_image"] = f"card_data/ui_card_{rid:06d}.png"
            else:
                raise ValueError(f"未知奖励类型: {kind}")
            counts[kind] += 1
            rewards.append(entry)
        catalog_characters.append(
            {
                "id": cid,
                "name": character["name"],
                "birthday": character.get("birthday", ""),
                "rewards": rewards,
            }
        )

    for kind, expected in EXPECTED.items():
        if counts[kind] != expected * 17:
            raise ValueError(f"{kind} 数量异常: {counts[kind]} != {expected * 17}")

    catalog = {
        "version": VERSION,
        "curve_version": CURVE_VERSION,
        "counts": counts,
        "characters": catalog_characters,
    }
    (destination / "reward_catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"counts": counts, "characters": len(catalog_characters)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).parents[1] / "assets/growth")
    parser.add_argument("--cards", type=Path, default=Path(__file__).parents[1] / "assets/card_data")
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output, args.cards), ensure_ascii=False))
