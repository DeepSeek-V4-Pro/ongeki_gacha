"""离线构建：python -m ongeki_gacha.tools.build_growth_catalog --source output/character_affection。"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from ..gacha_core import CardInfo
from ..growth_core import CURVE_VERSION, MAIN_CHARACTER_IDS, build_thresholds
from ..gacha_pools import GachaSchedule


def build(source: Path, card_root: Path, destination: Path) -> dict:
    hashes = {}

    def read(path: Path):
        content = path.read_bytes()
        hashes[path.name] = hashlib.sha256(content).hexdigest()
        return json.loads(content.decode("utf-8-sig"))

    points = read(source / "tables/IntimateLevelPointTableRecord.json")
    scales = read(source / "tables/IntimateLevelScaleTableRecord.json")
    thresholds = build_thresholds(
        [row["Point"] for row in sorted(points, key=lambda r: r["EnumValue"])],
        [row["Scale"] for row in sorted(scales, key=lambda r: r["EnumValue"])])
    for level, expected in {10: 3000, 20: 9000, 50: 45000, 100: 165000, 200: 363000}.items():
        if thresholds[level] != expected:
            raise ValueError(f"曲线锚点失败: Lv{level}")
    favorites = read(source / "tables/FavoriteCharaTableRecord.json")
    if {r["Value"] for r in favorites} != MAIN_CHARACTER_IDS:
        raise ValueError("主角色表与固定白名单不一致")
    characters = read(source / "character_affection.json")
    if len(characters) != 17 or {int(c["id"]) for c in characters} != MAIN_CHARACTER_IDS:
        raise ValueError("互动角色目录必须恰好包含17名主角色")
    raw_characters = read(source / "data/chara.json")
    counts = Counter(int(c["Name"]["id"]) for c in raw_characters)
    chara_by_id = {int(c["Name"]["id"]): c for c in raw_characters}
    cards = [CardInfo.from_dict(c) for c in read(card_root / "card_info_merged.json")]
    card_by_id = {c.id: c for c in cards}
    if len(card_by_id) != len(cards):
        raise ValueError("卡表存在重复ID")
    mapping = []
    for card in cards:
        known = counts[card.character_id] == 1
        main = known and card.character_id in MAIN_CHARACTER_IDS
        mapping.append({"card_id": card.id, "character_id": card.character_id,
                        "affection_enabled": main,
                        "bloom_policy": "main_affection" if main else "material_only" if known else "blocked_unmapped",
                        "source": "card_info_merged.json:charaId",
                        "reason": "" if known else "missing_or_ambiguous_character",
                        "image_present": (card_root / card.image_file).is_file()})
    for character in characters:
        cid = int(character["id"])
        raw = chara_by_id[cid]
        if counts[cid] != 1 or str(raw["IsCommunicationTarget"]).lower() != "true":
            raise ValueError(f"主角色互动配置无效: {cid}")
        reward_card = int(raw["FirstContactRewardCardData"]["id"])
        card = card_by_id.get(reward_card)
        if card is None or card.rarity != "N" or card.character_id != cid or not (card_root / card.image_file).is_file():
            raise ValueError(f"主角色奖励卡或图片无效: {cid}/{reward_card}")
        slots = Counter()
        for reward in character["rewards"]:
            level = int(reward["level"])
            if not 0 < level <= 1000:
                raise ValueError("奖励等级越界")
            slot = slots[level]
            slots[level] += 1
            reward["reward_key"] = f"{cid}:{level}:{slot}"
            if reward["kind"] == "NormalCard" and int(reward["id"]) != reward_card:
                raise ValueError(f"奖励卡关联不一致: {cid}")
    schedule_path = card_root / "gacha_pools.json"
    read(schedule_path)
    schedule = GachaSchedule.load(schedule_path)
    mapping_by_id = {m["card_id"]: m for m in mapping}
    coverage = []
    for pool in (schedule.regular_pool, schedule.non_gacha_pool, *schedule.entries):
        if pool is None:
            continue
        coverage.append({"pool_id": pool.pool_id, "name": pool.name,
                         "candidate_count": len(pool.cards),
                         "missing_cards": sorted(set(pool.cards) - card_by_id.keys()),
                         "excluded_from_new_bloom": [cid for cid in sorted(pool.cards)
                              if cid in mapping_by_id and mapping_by_id[cid]["bloom_policy"] == "blocked_unmapped"]})
    report = {"version": CURVE_VERSION, "card_count": len(cards),
              "policies": dict(Counter(m["bloom_policy"] for m in mapping)),
              "unmapped": [m for m in mapping if m["bloom_policy"] == "blocked_unmapped"],
              "missing_card_images": [m["card_id"] for m in mapping if not m["image_present"]],
              "source_sha256": hashes,
              "pool_coverage": coverage,
              "exclusion_policy": "未知映射仍可收集升星并继承旧阶段，阻断新解花，不能默认免好感。",
    "pending": ["57张卡的31个角色ID缺少独立角色配置佐证，暂列排除", "奖励装饰及头像素材校验", "170条语音cue映射和QQ联调", "四类用户经济模拟", "三张视觉样稿"]}
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in {"character_catalog": {"version": CURVE_VERSION, "characters": characters},
                       "card_character_map": {"version": CURVE_VERSION, "cards": mapping},
                       "affection_curve": {"version": CURVE_VERSION, "thresholds": thresholds, "source_sha256": hashes},
                       "build_report": report}.items():
        (destination / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--card-root", type=Path, default=Path(__file__).parents[1] / "assets/card_data")
    parser.add_argument("--output", type=Path, default=Path(__file__).parents[1] / "assets/growth")
    args = parser.parse_args()
    result = build(args.source, args.card_root, args.output)
    print(json.dumps({k: result[k] for k in ("card_count", "policies", "missing_card_images", "pending")}, ensure_ascii=False, indent=2))
