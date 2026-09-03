"""抽卡核心规则：卡牌加载、两阶段抽取与保底。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import json
import random

RARITIES = ("N", "R", "SR", "SRPlus", "SSR")
SR_OR_ABOVE = frozenset({"SR", "SRPlus", "SSR"})
RARITY_ORDER = {"SSR": 0, "SRPlus": 1, "SR": 2, "R": 3, "N": 4}


@dataclass(frozen=True)
class CardInfo:
    """单张卡牌的基础信息。"""

    id: int
    name: str
    rarity: str
    image_file: str
    attribute: str = ""
    version: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CardInfo":
        """从合并 JSON 记录构造卡牌信息。"""
        card_id = raw.get("id")
        if card_id is None:
            raise ValueError("卡牌 JSON 中缺少 id")
        return cls(
            id=int(card_id),
            name=str(raw.get("name") or f"卡牌 {card_id}"),
            rarity=str(raw.get("rarity") or "R").strip(),
            image_file=str(raw.get("imageFile") or f"ui_card_{int(card_id):06d}.png"),
            attribute=str(raw.get("attribute") or ""),
            version=str(raw.get("version") or ""),
        )


@dataclass(frozen=True)
class CardCollection:
    """从 JSON 加载的全部卡牌集合。"""

    cards: tuple[CardInfo, ...]
    by_id: dict[int, CardInfo] = field(default_factory=dict)
    by_rarity: dict[str, tuple[CardInfo, ...]] = field(default_factory=dict)

    @classmethod
    def from_list(cls, items: Iterable[CardInfo]) -> "CardCollection":
        """构造卡牌集合并建立索引。"""
        cards = tuple(items)
        by_id = {card.id: card for card in cards}
        by_rarity: dict[str, list[CardInfo]] = {}
        for card in cards:
            by_rarity.setdefault(card.rarity, []).append(card)
        return cls(
            cards=cards,
            by_id=by_id,
            by_rarity={rarity: tuple(values) for rarity, values in by_rarity.items()},
        )


def load_cards(json_path: Path) -> CardCollection:
    """读取卡牌信息 JSON，并过滤无图片的异常记录。"""
    if not json_path.is_file():
        raise FileNotFoundError(f"卡牌信息文件不存在: {json_path}")
    with json_path.open("r", encoding="utf-8") as file_obj:
        raw_data = json.load(file_obj)
    if not isinstance(raw_data, list):
        raise ValueError("卡牌信息 JSON 顶层必须是数组")

    cards: list[CardInfo] = []
    for raw_card in raw_data:
        if not isinstance(raw_card, dict):
            continue
        if not bool(raw_card.get("imagePresent", False)):
            continue
        try:
            cards.append(CardInfo.from_dict(raw_card))
        except (TypeError, ValueError):
            continue

    if not cards:
        raise ValueError("卡牌信息 JSON 中没有可用卡牌")
    return CardCollection.from_list(cards)


def rarity_display(rarity: str) -> str:
    """将内部稀有度值转换为展示文本。"""
    if rarity == "SRPlus":
        return "SR+"
    return rarity


def max_detail_slots(rarity: str) -> int:
    """返回满突破所需的槽位数：N 为 11，其余为 5。"""
    return 11 if rarity == "N" else 5


def derive_growth(rarity: str, copies: int) -> tuple[int, bool, bool]:
    """根据持有数量推导星级、解花与超解花状态。"""
    max_slots = max_detail_slots(rarity)
    stars = min(max(copies, 0), max_slots)
    is_kaika = copies >= max_slots + 1
    is_cho_kaika = copies >= max_slots + 2
    return stars, is_kaika, is_cho_kaika


class CardPool:
    """全卡大混池的两阶段抽取器。"""

    def __init__(
        self,
        cards: CardCollection,
        *,
        weight_n: int,
        weight_r: int,
        weight_sr: int,
        weight_sr_plus: int,
        weight_ssr: int,
    ) -> None:
        raw_weights = {
            "N": weight_n,
            "R": weight_r,
            "SR": weight_sr,
            "SRPlus": weight_sr_plus,
            "SSR": weight_ssr,
        }
        self._cards = cards
        self._rarity_weights: list[tuple[str, int]] = []
        self._candidates: dict[str, list[CardInfo]] = {}
        for rarity in RARITIES:
            weight = int(raw_weights.get(rarity, 0) or 0)
            candidates = list(cards.by_rarity.get(rarity, ()))
            if weight > 0 and candidates:
                self._rarity_weights.append((rarity, weight))
                self._candidates[rarity] = candidates
        if not self._rarity_weights:
            raise ValueError("没有配置任何有效的稀有度权重或候选卡牌")

        self._guarantee_weights: list[tuple[str, int]] = []
        self._guarantee_candidates: dict[str, list[CardInfo]] = {}
        for rarity in ("SR", "SRPlus", "SSR"):
            weight = int(raw_weights.get(rarity, 0) or 0)
            candidates = list(cards.by_rarity.get(rarity, ()))
            if weight > 0 and candidates:
                self._guarantee_weights.append((rarity, weight))
                self._guarantee_candidates[rarity] = candidates

    def _weighted_rarity(self, pool: list[tuple[str, int]]) -> str:
        rarities = [rarity for rarity, _ in pool]
        weights = [weight for _, weight in pool]
        return random.choices(rarities, weights=weights, k=1)[0]

    def _pick_card(self, rarity: str) -> CardInfo:
        candidates = self._candidates.get(rarity)
        if not candidates:
            raise RuntimeError(f"稀有度 {rarity} 没有候选卡牌")
        return random.choice(candidates)

    def draw(self, count: int, guarantee: bool = True) -> list[CardInfo]:
        """抽取指定数量的卡牌，并应用 5/11 连 SR 或以上保底。"""
        if count <= 0:
            raise ValueError("抽卡数量必须大于 0")
        results = [self._pick_card(self._weighted_rarity(self._rarity_weights)) for _ in range(count)]

        if guarantee and count in (5, 11) and not any(card.rarity in SR_OR_ABOVE for card in results):
            if not self._guarantee_weights:
                raise RuntimeError("保底所需 SR 或以上稀有度权重为空")
            guarantee_rarity = self._weighted_rarity(self._guarantee_weights)
            guarantee_candidates = self._guarantee_candidates.get(guarantee_rarity)
            if not guarantee_candidates:
                raise RuntimeError(f"保底稀有度 {guarantee_rarity} 没有候选卡牌")
            replacement = random.choice(guarantee_candidates)
            slot = random.randrange(len(results))
            results[slot] = replacement
        return results

    @property
    def rarity_weights(self) -> tuple[tuple[str, int], ...]:
        """返回当前生效的稀有度权重。"""
        return tuple(self._rarity_weights)

    @property
    def guarantee_weights(self) -> tuple[tuple[str, int], ...]:
        """返回保底使用的 SR 或以上稀有度权重。"""
        return tuple(self._guarantee_weights)
