"""ONGEKI gacha schedule loading and active-pool selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import json


RARITY_ALIASES = {
    "1": "N",
    "2": "R",
    "3": "SR",
    "4": "SSR",
    "SR+": "SRPlus",
}


@dataclass(frozen=True)
class PoolCard:
    """Per-card weight metadata inside a gacha pool."""

    card_id: int
    rarity: str = ""
    version: str = ""
    card_number: str = ""
    weight: int = 1
    is_pickup: bool = False
    is_select: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PoolCard":
        raw_rarity = str(raw.get("rarity") or "")
        return cls(
            card_id=int(raw["card_id"]),
            rarity=RARITY_ALIASES.get(raw_rarity, raw_rarity),
            version=str(raw.get("version") or ""),
            card_number=str(raw.get("cardNumber") or ""),
            weight=max(int(raw.get("weight") or 1), 1),
            is_pickup=bool(raw.get("is_pickup", False)),
            is_select=bool(raw.get("is_select", False)),
        )


@dataclass(frozen=True)
class PoolEntry:
    """One official/special gacha pool."""

    pool_id: str
    name: str
    kind: str = "special"
    start_date: date | None = None
    end_date: date | None = None
    select_points: int | None = None
    article_url: str = ""
    image_url: str = ""
    cards: dict[int, PoolCard] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PoolEntry":
        start = raw.get("start_date")
        end = raw.get("end_date")
        cards_data = raw.get("cards") or []
        cards = {
            int(item["card_id"]): PoolCard.from_dict(item)
            for item in cards_data
            if item.get("card_id") is not None
        }
        start = datetime.fromisoformat(start).date() if start else None
        end = datetime.fromisoformat(end).date() if end else None
        return cls(
            pool_id=str(raw.get("id") or raw.get("page_id") or ""),
            name=str(raw.get("name") or ""),
            kind=str(raw.get("kind") or "special"),
            start_date=start,
            end_date=end,
            select_points=(
                int(raw["select_points"])
                if raw.get("select_points")
                else None
            ),
            article_url=str(raw.get("article_url") or ""),
            image_url=str(raw.get("image_url") or ""),
            cards=cards,
        )

    def is_active(self, day: date) -> bool:
        if self.start_date is None or self.end_date is None:
            return False
        return self.start_date <= day < self.end_date

    @property
    def featured_count(self) -> int:
        """Return the number of pickup cards in this pool."""
        return sum(1 for card in self.cards.values() if card.is_pickup)

    @property
    def select_count(self) -> int:
        """Return the number of selectable cards in this pool."""
        return sum(1 for card in self.cards.values() if card.is_select)

    def up_ssr_cards(self) -> list[PoolCard]:
        """Return pickup SSR cards for this pool, sorted by card id."""
        return sorted(
            (
                card
                for card in self.cards.values()
                if card.is_pickup and card.rarity == "SSR"
            ),
            key=lambda item: item.card_id,
        )


@dataclass(frozen=True)
class GachaSchedule:
    """Sorted collection of official gacha pools."""

    entries: tuple[PoolEntry, ...] = ()
    default_pool_id: str = "regular"
    by_id: dict[str, PoolEntry] = field(default_factory=dict)
    regular_pool: PoolEntry | None = None
    non_gacha_pool: PoolEntry | None = None

    def __post_init__(self) -> None:
        ordered = tuple(
            sorted(
                self.entries,
                key=lambda item: (item.start_date or date.min, item.pool_id),
            )
        )
        object.__setattr__(self, "entries", ordered)
        object.__setattr__(
            self,
            "by_id",
            {item.pool_id: item for item in ordered},
        )
        merged_by_id = dict(self.by_id)
        if self.regular_pool is not None:
            merged_by_id[self.regular_pool.pool_id] = self.regular_pool
        object.__setattr__(self, "by_id", merged_by_id)

    @classmethod
    def load(cls, path: Path) -> "GachaSchedule":
        """Load schedule JSON; a missing/corrupt file degrades to empty."""
        if not path.is_file():
            return cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return cls()
        rows = payload.get("pools") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return cls()
        regular_raw = payload.get("regular_pool") if isinstance(payload, dict) else None
        non_gacha_raw = payload.get("non_gacha_pool") if isinstance(payload, dict) else None
        regular_pool = (
            PoolEntry.from_dict(regular_raw)
            if isinstance(regular_raw, dict) and regular_raw.get("id")
            else None
        )
        non_gacha_pool = (
            PoolEntry.from_dict(non_gacha_raw)
            if isinstance(non_gacha_raw, dict) and non_gacha_raw.get("id")
            else None
        )
        return cls(
            entries=tuple(
                PoolEntry.from_dict(item)
                for item in rows
                if isinstance(item, dict) and item.get("id")
            ),
            default_pool_id=str(payload.get("default_pool_id") or "regular"),
            regular_pool=regular_pool,
            non_gacha_pool=non_gacha_pool,
        )

    def get(self, pool_id: str) -> PoolEntry | None:
        return self.by_id.get(pool_id)

    def active_for(self, day: date) -> PoolEntry | None:
        """Return the most recently started official pool active on ``day``."""
        active = [entry for entry in self.entries if entry.is_active(day)]
        if not active:
            return None
        return max(active, key=lambda item: item.start_date or date.min)

    def active_for_all(self, day: date) -> tuple[PoolEntry, ...]:
        """Return every official pool active on ``day``."""
        return tuple(
            entry
            for entry in self.entries
            if entry.is_active(day)
        )

    def cycle_for(
        self,
        day: date,
        interval_days: int = 7,
        epoch: date | None = None,
    ) -> PoolEntry | None:
        """Return a deterministic historical pool for a rotation interval."""
        if not self.entries:
            return None
        interval = max(int(interval_days), 1)
        base = epoch or date.min
        elapsed_days = max((day - base).days, 0)
        index = (elapsed_days // interval) % len(self.entries)
        return self.entries[index]
