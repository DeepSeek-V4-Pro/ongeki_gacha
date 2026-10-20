"""版本变更补偿指令：每个版本、每名有历史数据的用户领取一次。"""
from __future__ import annotations

from typing import Any

from maibot_sdk import Command

from .version_rewards import pending_rewards


class CompensationCommandsMixin:
    """领取版本补偿，按版本去重；成功时优先发物品图，无素材时回退文字。"""

    @Command(
        "ongeki_version_reward",
        description="领取本次版本更新的补偿（每个版本每账号一次，需在上个版本已有数据）",
        pattern=r"^/(?:版本补偿|补偿)\s*$",
    )
    async def handle_version_reward(
        self, stream_id: str = "", **kwargs: dict[str, Any]
    ) -> tuple[bool, str, bool]:
        user_id = self._user_id(kwargs)
        if self._db is None:
            text = "插件未就绪，请稍后重试"
            await self._send_text(stream_id, text)
            return True, text, True
        granted: list = []
        points = 0
        rejected = ""
        async with self._lock:
            pending = pending_rewards(self._db.claimed_versions(user_id))
            if not pending:
                text = "当前没有可领取的版本补偿"
                await self._send_text(stream_id, text)
                return True, text, True
            for reward in pending:
                baseline = self._db.ensure_version_baseline(reward.version)
                outcome = self._db.claim_version_reward(
                    user_id,
                    reward.version,
                    points=reward.points,
                    items=reward.items,
                    baseline_at=baseline,
                )
                if outcome.get("success"):
                    granted.append(reward)
                    points = int(outcome.get("points") or points)
                elif outcome.get("error") == "not_eligible" and not rejected:
                    rejected = f"{reward.version} 之前没有你的数据，无法领取该版本补偿"
                elif outcome.get("error") == "no_data" and not rejected:
                    rejected = "没有你的账号数据，先 /签到 再领取"
        if not granted:
            text = rejected or "补偿发放失败，请稍后重试"
            await self._send_text(stream_id, text)
            return True, text, True

        text = "版本补偿：" + "；".join(
            f"{reward.version}｜{reward.summary()}" for reward in granted
        )
        gained_points = sum(int(reward.points) for reward in granted)
        if points:
            text += f"\n当前点数：{points}"
        items: dict[str, int] = {}
        for reward in granted:
            for item, quantity in reward.items.items():
                items[item] = items.get(item, 0) + int(quantity)
        versions = "、".join(reward.version for reward in granted)
        subtitle_parts = []
        if gained_points:
            subtitle_parts.append(f"+{gained_points} 点")
        if points:
            subtitle_parts.append(f"当前 {points} 点")
        sent = False
        if items:
            # 有素材时发物品图；字体或素材缺失、发送失败都回退到上面的文字。
            sent = await self._send_item_gain_card(
                stream_id,
                user_id,
                items,
                title="版本补偿 · " + versions,
                subtitle="｜".join(subtitle_parts),
            )
        if not sent:
            await self._send_text(stream_id, text)
        return True, text, True
