"""运行时静态养成目录；拒绝版本漂移和错误角色归属。"""
from __future__ import annotations

import json
from pathlib import Path

from .gacha_core import CardCollection
from .growth_core import (
    CURVE_VERSION,
    FRAGMENT_RATES,
    MAIN_CHARACTER_IDS,
    MAX_AFFECTION_LEVEL,
    OVERFLOW_FRAGMENT_RATES,
)
from .starter_cards import STARTER_CARDS


class GrowthCatalog:
    def __init__(self, root: Path, cards: CardCollection, rules_override: dict | None = None):
        def read(name):
            return json.loads((root / name).read_text(encoding="utf-8"))
        character_data = read("character_catalog.json")
        mapping_data = read("card_character_map.json")
        curve_data = read("affection_curve.json")
        if any(d["version"] != CURVE_VERSION for d in (character_data, mapping_data, curve_data)):
            raise ValueError("养成目录版本不一致")
        self.characters = {int(c["id"]): c for c in character_data["characters"]}
        if set(self.characters) != MAIN_CHARACTER_IDS or len(character_data["characters"]) != 17:
            raise ValueError("主角色目录不符合固定白名单")
        for cid,card_id in STARTER_CARDS.items():
            reward_ids={int(r['id']) for r in self.characters[cid]['rewards'] if r['kind']=='NormalCard'}
            if reward_ids != {card_id} or card_id not in cards.by_id or cards.by_id[card_id].rarity != 'N' or cards.by_id[card_id].character_id != cid:
                raise ValueError(f'基础N卡与好感奖励不一致: {cid}')
        self.mapping = {int(c["card_id"]): c for c in mapping_data["cards"]}
        if len(self.mapping) != len(mapping_data["cards"]):
            raise ValueError("卡牌映射ID重复")
        self.thresholds = tuple(curve_data["thresholds"])
        if len(self.thresholds) != MAX_AFFECTION_LEVEL + 1 or self.thresholds[0] != 0 or any(
            type(v) is not int or v <= self.thresholds[i] for i, v in enumerate(self.thresholds[1:])):
            raise ValueError(f"好感阈值必须为{MAX_AFFECTION_LEVEL + 1}项严格递增整数")
        self.rules = read("rules_draft.json")
        if rules_override:
            self.rules.update({key: value for key, value in rules_override.items() if value is not None})
        self._validate_rules(self.rules)
        if self.rules["curve_version"] != CURVE_VERSION:
            raise ValueError("规则与好感曲线版本不一致")
        if self.rules["duplicate_fragments"] != FRAGMENT_RATES or self.rules["overflow_fragments"] != OVERFLOW_FRAGMENT_RATES:
            raise ValueError("碎片规则与版本化计算器不一致")
        self.cards = cards
        for card in cards.cards:
            mapping = self.mapping.get(card.id)
            if mapping is None or mapping["character_id"] != card.character_id:
                raise ValueError(f"卡表与养成目录归属不一致: {card.id}")
            expected_main = card.character_id in MAIN_CHARACTER_IDS
            if mapping["bloom_policy"] not in {"main_affection", "material_only", "blocked_unmapped"}:
                raise ValueError("未知解花策略")
            expected_policy = "main_affection" if expected_main else "blocked_unmapped" if card.character_id is None or mapping.get("reason") == "missing_or_ambiguous_character" else "material_only"
            if mapping["bloom_policy"] != expected_policy:
                raise ValueError(f"主角色策略不一致: {card.id}")
        for cid, character in self.characters.items():
            keys = set()
            for reward in character["rewards"]:
                if reward["reward_key"] in keys or not 0 < int(reward["level"]) <= 1000:
                    raise ValueError("奖励键重复或等级越界")
                keys.add(reward["reward_key"])
                if reward["kind"] not in {"NormalCard", "ProfileVoice", "Trophy", "NamePlate", "Attachment"}:
                    raise ValueError("奖励类型未实现")
                if reward["kind"] == "NormalCard":
                    card = cards.by_id.get(int(reward["id"]))
                    if card is None or card.character_id != cid or card.rarity != "N":
                        raise ValueError("奖励卡归属错误")

    @staticmethod
    def _validate_rules(rules):
        def integer(value, name, minimum=0):
            if type(value) is not int or not minimum <= value <= 1_000_000:
                raise ValueError(f"养成规则 {name} 必须为范围内整数")
        for name in ("monthly_event_days", "monthly_event_fragments",
                     "task_medium_gifts_daily_cap", "task_fragments_daily_cap"):
            integer(rules.get(name), name)
        integer(rules.get("companion_points"), "companion_points", 1)
        integer(rules.get('ultimate_large_gifts_lifetime_cap'),'ultimate_large_gifts_lifetime_cap')
        event_days = rules["monthly_event_days"]
        seen = set()
        for name in ("monthly_event_small_gift_days", "monthly_event_medium_gift_days",
                     "monthly_event_large_gift_days"):
            days = rules.get(name)
            if not isinstance(days, list) or any(type(day) is not int or not 1 <= day <= event_days for day in days):
                raise ValueError(f"养成规则 {name} 日期越界")
            if seen & set(days):
                raise ValueError(f"养成规则 {name} 与其它礼物日重复")
            seen |= set(days)
        purchase = rules.get("gift_purchase")
        if not isinstance(purchase, dict) or not purchase or not set(purchase) <= {"small", "medium"}:
            raise ValueError("养成规则 gift_purchase 只支持小礼物与中礼物")
        for size, plan in purchase.items():
            if not isinstance(plan, dict) or set(plan) != {"price", "weekly_cap"}:
                raise ValueError(f"养成规则 gift_purchase.{size} 字段不完整")
            integer(plan.get("price"), f"gift_purchase.{size}.price", 1)
            integer(plan.get("weekly_cap"), f"gift_purchase.{size}.weekly_cap", 1)
        sources=rules.get('task_medium_gift_sources')
        if not isinstance(sources, list) or not sources or len(set(sources)) != len(sources) or not set(sources) <= {"normal", "challenge", "advanced", "ultimate"}:
            raise ValueError('中礼物任务来源无效')
        for name in ("duplicate_fragments", "overflow_fragments"):
            values = rules.get(name)
            if not isinstance(values, dict) or set(values) != set(FRAGMENT_RATES):
                raise ValueError(f"养成规则 {name} 稀有度不完整")
            for key, value in values.items():
                integer(value, f"{name}.{key}", 1)
        for name, keys in (("gift_points", {"small", "medium", "large"}),
                           ("task_fragments", {"normal", "challenge", "advanced", "ultimate"})):
            values = rules.get(name)
            if not isinstance(values, dict) or set(values) != keys:
                raise ValueError(f"养成规则 {name} 项目不完整")
            for key, value in values.items():
                integer(value, f"{name}.{key}", 1 if name == "gift_points" else 0)
        bloom_items = rules.get("bloom_items")
        allowed_items = {"gift_small", "gift_medium", "gift_large", "flower_fragment", "bloom_ticket"}
        if not isinstance(bloom_items, list) or len(bloom_items) != 2 or not set(bloom_items) <= allowed_items:
            raise ValueError("养成规则 bloom_items 必须为两个已知物品ID")
        for name in ("bloom_levels", "bloom_costs"):
            values = rules.get(name)
            if not isinstance(values, list) or len(values) != 2:
                raise ValueError(f"养成规则 {name} 必须有两个阶段")
            for value in values:
                integer(value, name, 1)
            if values[0] > values[1] or name == "bloom_levels" and values[1] > 1000:
                raise ValueError(f"养成规则 {name} 阶段顺序或等级越界")
        ticket = rules.get("bloom_ticket_source")
        if not isinstance(ticket, dict) or set(ticket) != {"kind", "min_level", "grade", "cooldown_days"}:
            raise ValueError("养成规则 bloom_ticket_source 字段不完整")
        if ticket.get("kind") not in {"advanced"}:
            raise ValueError("解花券来源目前只支持高级挑战")
        if not isinstance(ticket.get("min_level"), (int, float)) or float(ticket["min_level"]) <= 0:
            raise ValueError("解花券来源最低定数无效")
        if ticket.get("grade") not in {"SSS", "SSS+"}:
            raise ValueError("解花券来源评级无效")
        integer(ticket.get("cooldown_days"), "bloom_ticket_source.cooldown_days")
