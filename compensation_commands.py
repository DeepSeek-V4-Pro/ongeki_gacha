"""版本变更补偿指令：每个版本、每名有历史数据的用户领取一次。"""
from __future__ import annotations

from typing import Any

from maibot_sdk import Command

from .version_rewards import pending_rewards


class CompensationCommandsMixin:
    """领取版本补偿，按版本去重。"""

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
        async with self._lock:
            pending = pending_rewards(self._db.claimed_versions(user_id))
            if not pending:
                text = "当前没有可领取的版本补偿"
                await self._send_text(stream_id, text)
                return True, text, True
            granted: list[str] = []
            points = 0
            rejected = ""
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
                    granted.append(f"{reward.version}｜{reward.summary()}")
                    points = int(outcome.get("points") or points)
                elif outcome.get("error") == "not_eligible" and not rejected:
                    rejected = f"{reward.version} 之前没有你的数据，无法领取该版本补偿"
                elif outcome.get("error") == "no_data" and not rejected:
                    rejected = "没有你的账号数据，先 /签到 再领取"
        if granted:
            text = "版本补偿：" + "；".join(granted)
            if points:
                text += f"\n当前点数：{points}"
        else:
            text = rejected or "补偿发放失败，请稍后重试"
        await self._send_text(stream_id, text)
        return True, text, True
