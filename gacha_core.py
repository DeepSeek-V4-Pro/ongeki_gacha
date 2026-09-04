"""抽卡核心规则：卡牌加载、两阶段抽取与保底。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import json
import random

from .gacha_pools import PoolEntry

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
    with json_path.open("r", encoding="utf-8-sig") as file_obj:
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
    """模拟抽卡池的两阶段抽取器。"""

    def __init__(
        self,
        cards: CardCollection,
        *,
        weight_n: int,
        weight_r: int,
        weight_sr: int,
        weight_sr_plus: int,
        weight_ssr: int,
        pool: PoolEntry | None = None,
        pickup_multiplier: int = 10,
        strict_pool_cards: bool = False,
    ) -> None:
        raw_weights = {
            "N": weight_n,
            "R": weight_r,
            "SR": weight_sr,
            "SRPlus": weight_sr_plus,
            "SSR": weight_ssr,
        }
        self._cards = cards
        self._raw_weights = raw_weights
        self._pool: PoolEntry | None = None
        self._pickup_multiplier = max(int(pickup_multiplier), 1)
        self._strict_pool_cards = bool(strict_pool_cards)
        self._rarity_weights: list[tuple[str, int]] = []
        self._candidates: dict[str, list[CardInfo]] = {}
        self._candidate_weights: dict[str, list[int]] = {}
        self.set_pool(pool)

    def _effective_weight(self, card: CardInfo) -> int:
        """Return the per-card weight inside the active pool."""
        pool_card = self._pool.cards.get(card.id) if self._pool is not None else None
        weight = int(pool_card.weight) if pool_card is not None else 1
        if pool_card is not None and pool_card.is_pickup:
            weight *= self._pickup_multiplier
        return max(int(weight), 1)

    def set_pool(self, pool: PoolEntry | None) -> None:
        """Rebuild rarity/candidate tables for a pool definition."""
        self._pool = pool
        self._rarity_weights = []
        self._candidates = {}
        self._candidate_weights = {}
        pool_card_ids = set(pool.cards) if pool is not None else set()

        for rarity in RARITIES:
            weight = int(self._raw_weights.get(rarity, 0) or 0)
            if weight <= 0:
                continue
            candidates: list[CardInfo] = []
            if self._strict_pool_cards and pool is not None:
                candidates = [
                    card
                    for card in self._cards.by_rarity.get(rarity, ())
                    if card.id in pool_card_ids
                ]
            else:
                candidates = list(self._cards.by_rarity.get(rarity, ()))
            if not candidates:
                continue
            self._rarity_weights.append((rarity, weight))
            self._candidates[rarity] = candidates
            self._candidate_weights[rarity] = [
                self._effective_weight(card) for card in candidates
            ]

        if not self._rarity_weights:
            raise ValueError("没有配置任何有效的稀有度权重或候选卡牌")

        self._guarantee_weights: list[tuple[str, int]] = []
        self._guarantee_candidates: dict[str, list[CardInfo]] = {}
        self._guarantee_candidate_weights: dict[str, list[int]] = {}
        for rarity in ("SR", "SRPlus", "SSR"):
            weight = int(self._raw_weights.get(rarity, 0) or 0)
            if weight <= 0:
                continue
            candidates = self._candidates.get(rarity, [])
            if not candidates:
                continue
            self._guarantee_weights.append((rarity, weight))
            self._guarantee_candidates[rarity] = candidates
            self._guarantee_candidate_weights[rarity] = [
                self._effective_weight(card) for card in candidates
            ]

    def _weighted_rarity(self, pool: list[tuple[str, int]]) -> str:
        rarities = [rarity for rarity, _ in pool]
        weights = [weight for _, weight in pool]
        return random.choices(rarities, weights=weights, k=1)[0]

    def _pick_card(self, rarity: str) -> CardInfo:
        candidates = self._candidates.get(rarity)
        weights = self._candidate_weights.get(rarity)
        if not candidates:
            raise RuntimeError(f"稀有度 {rarity} 没有候选卡牌")
        return random.choices(candidates, weights=weights, k=1)[0]

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
            guarantee_weights = self._guarantee_candidate_weights.get(guarantee_rarity)
            if not guarantee_candidates:
                raise RuntimeError(f"保底稀有度 {guarantee_rarity} 没有候选卡牌")
            replacement = random.choices(
                guarantee_candidates,
                weights=guarantee_weights,
                k=1,
            )[0]
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

    @property
    def pool_id(self) -> str:
        return self._pool.pool_id if self._pool is not None else "regular"

    @property
    def pool_name(self) -> str:
        if self._pool is None:
            return "レギュラーガチャ（全卡池）"
        return self._pool.name

    @property
    def pool_kind(self) -> str:
        return self._pool.kind if self._pool is not None else "regular"

    @property
    def pool_start_date(self) -> str:
        return self._pool.start_date.isoformat() if self._pool and self._pool.start_date else ""

    @property
    def pool_end_date(self) -> str:
        return self._pool.end_date.isoformat() if self._pool and self._pool.end_date else ""

    @property
    def pool_select_points(self) -> int | None:
        return self._pool.select_points if self._pool is not None else None

    @property
    def featured_count(self) -> int:
        if self._pool is None:
            return 0
        return sum(1 for card in self._pool.cards.values() if card.is_pickup)

    @property
    def pickup_multiplier(self) -> int:
        return self._pickup_multiplier
