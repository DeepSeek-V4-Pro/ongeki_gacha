"""好感奖励目录：称号/名牌/装饰/档案语音/奖励卡，供账号档案与奖励展示。"""
from __future__ import annotations

import json
from pathlib import Path

from .growth_core import CURVE_VERSION, MAIN_CHARACTER_IDS

VERSION = "reward-catalog-v1"


class RewardCatalog:
    def __init__(self, root: Path):
        self.root = root.resolve()
        data = json.loads((self.root / "reward_catalog.json").read_text(encoding="utf-8"))
        if data.get("version") != VERSION:
            raise ValueError("奖励目录版本不一致")
        if data.get("curve_version") != CURVE_VERSION:
            raise ValueError("奖励目录与好感曲线版本不一致")
        characters = {int(c["id"]): c for c in data.get("characters", [])}
        if set(characters) != MAIN_CHARACTER_IDS:
            raise ValueError("奖励目录必须覆盖17名主角色")
        plugin_assets = self.root.parent
        self.characters = characters
        self.by_key: dict[str, dict] = {}
        # 素材（图、语音、卡面）不随发布包分发，缺失只记录不报错；
        # 渲染时会因为读不到文件抛错，由命令层回退为文字。
        self.missing_assets: list[str] = []
        for character in characters.values():
            for reward in character["rewards"]:
                key = str(reward["reward_key"])
                if key in self.by_key:
                    raise ValueError(f"奖励键重复: {key}")
                self.by_key[key] = reward
                for field in ("image", "icon", "audio"):
                    if field in reward and not (self.root / reward[field]).is_file():
                        self.missing_assets.append(str(reward[field]))
                if "card_image" in reward and not (plugin_assets / reward["card_image"]).is_file():
                    self.missing_assets.append(str(reward["card_image"]))

    def rewards(self, cid: int) -> list[dict]:
        return self.characters[cid]["rewards"]

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for reward in self.by_key.values():
            counts[reward["kind"]] = counts.get(reward["kind"], 0) + 1
        return counts
