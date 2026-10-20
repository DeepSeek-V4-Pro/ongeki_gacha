"""版本变更补偿：每个版本、每名用户只能领取一次。"""
from __future__ import annotations

from dataclasses import dataclass, field

# 与 _manifest.json / metadata.yaml / config_model 的 plugin.config_version 同步。
PLUGIN_VERSION = "1.3.6"

ITEM_LABELS = {
    "bloom_ticket": "解花券",
    "gift_large": "大礼物",
    "gift_medium": "中礼物",
    "gift_small": "小礼物",
    "flower_fragment": "花之碎片",
}


@dataclass(frozen=True)
class VersionReward:
    """一个版本的补偿内容。"""

    version: str
    points: int = 0
    items: dict[str, int] = field(default_factory=dict)
    note: str = ""

    def item_text(self) -> str:
        return "、".join(
            f"{ITEM_LABELS.get(item, item)}×{count}"
            for item, count in self.items.items()
            if count > 0
        )

    def summary(self) -> str:
        parts = []
        if self.points:
            parts.append(f"{self.points} 点")
        if self.items:
            item_text = self.item_text()
            if item_text:
                parts.append(item_text)
        return "、".join(parts) or "无"


VERSION_REWARDS: tuple[VersionReward, ...] = (
    # 1.3.6：曲目搜索修复、半压缩档位、卡池默认 official、月周期签到。
    VersionReward(
        "1.3.6",
        points=2000,
        items={"bloom_ticket": 2, "gift_large": 3},
        note="2 张解花券、2000 点、3 份大礼物",
    ),
)


def pending_rewards(claimed) -> list[VersionReward]:
    """按版本顺序返回尚未领取的补偿。"""
    done = set(claimed or ())
    return [reward for reward in VERSION_REWARDS if reward.version not in done]
