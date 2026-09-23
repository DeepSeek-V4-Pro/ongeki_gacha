# -*- coding: utf-8 -*-
"""随机任务命令层。"""

from __future__ import annotations

import re
from typing import Any

from maibot_sdk import Command

from .gacha_db import GachaDatabase
from .task_catalog import GAME_LABELS, pick_random_task


def _growth_reward_text(medium_gifts: int, large_gifts: int, fragments: int) -> str:
    """只列实际发放的养成物品；全为 0 时返回空串。"""
    parts = []
    if medium_gifts:
        parts.append(f"中礼物 ×{medium_gifts}")
    if large_gifts:
        parts.append(f"大礼物 ×{large_gifts}")
    if fragments:
        parts.append(f"花之碎片 +{fragments}")
    return f"\n养成奖励：{'，'.join(parts)}" if parts else ""


class TaskCommandsMixin:
    """随机任务命令。"""

    @Command(
        "ongeki_task_accept",
        description="接取音游随机任务",
    pattern=r"^/接任务\s+(?P<kind>高级挑战|普通|挑战|终极)(?:\s+(?P<game>\S+))?\s*$",
    )
    async def handle_task_accept(
        self,
        stream_id: str = "",
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        user_id = self._user_id(kwargs)
        task_kind = self._task_kind_from_kwargs(kwargs)
        game = self._task_game_from_kwargs(kwargs)
        task_config = self.config.task
        if not task_config.enabled:
            text = "随机任务功能未启用"
            await self._send_text(stream_id, text)
            return True, text, True

        async with self._lock:
            if self._db is None:
                text = "插件未就绪，请稍后重试"
                await self._send_text(stream_id, text)
                return True, text, True
            catalog = await self._get_task_catalog()
            if not catalog:
                text = "任务曲库获取失败，请稍后重试"
                await self._send_text(stream_id, text)
                return True, text, True

            incomplete = self._db.list_tasks(
                user_id,
                statuses=("active", "submitted"),
            )
            incomplete_keys = {
                f"{task.game}:{task.song_id}" for task in incomplete
            }
            completed_keys: set[str] = set()
            if task_kind == "ultimate":
                progress = self._db.get_ultimate_progress(user_id)
                if progress.active_task_id is not None:
                    text = "已有未完成或待审核的终极任务"
                    await self._send_text(stream_id, text)
                    return True, text, True
                completed_keys = self._db.get_ultimate_completed_keys(user_id)

            selection = pick_random_task(
                catalog,
                task_kind,
                completed_keys=completed_keys,
                excluded_keys=incomplete_keys,
                game=game,
                challenge_min_level=self.config.task.challenge_min_level,
                advanced_min_level=self.config.task.advanced_min_level,
                ultimate_min_level=self.config.task.ultimate_min_level,
            )
            if selection is None:
                if task_kind == "ultimate":
                    self._db.set_ultimate_finished(user_id, True)
                    reason = (
                        "该游戏终极曲目已全部完成"
                        if game
                        else "终极曲目已全部完成"
                    )
                else:
                    reason = "该游戏候选任务不足" if game else "候选任务不足"
                text = f"{reason}，无法接取任务"
                await self._send_text(stream_id, text)
                return True, text, True

            chart = selection.chart
            reward = self._task_reward(task_kind)
            receipt = self._db.create_task(
                user_id,
                task_kind=task_kind,
                game=selection.song.game,
                song_id=selection.song.song_id,
                song_title=selection.song.title,
                artist=selection.song.artist,
                difficulty_index=chart.index if chart is not None else None,
                difficulty_label=(
                    chart.label
                    if chart is not None
                    else ""
                ),
                target_level=(
                    chart.level_display
                    if chart is not None
                    else ""
                ),
                target_level_value=(
                    chart.level_value
                    if chart is not None
                    else 0.0
                ),
                requirement_text=self._task_requirement(selection, task_kind),
                reward=reward,
                cover_url=selection.song.cover_url,
                normal_limit=task_config.normal_count,
                challenge_limit=task_config.challenge_count,
                advanced_limit=task_config.advanced_count,
                tz_offset_hours=self.config.economy.tz_offset_hours,
            )
            if not receipt.success:
                text = receipt.error or "接取任务失败"
                await self._send_text(stream_id, text)
                return True, text, True
            image_base64, text, card_complete = await self._render_task_card(
                receipt.task_id,
                selection,
                task_kind,
                user_id,
            )
            if image_base64:
                try:
                    await self.ctx.send.image(image_base64, stream_id)
                except Exception as exc:
                    self.ctx.logger.warning("任务卡发送失败，回退文本: %s", exc)
                    await self._send_text(stream_id, text)
                else:
                    if not card_complete:
                        await self._send_text(stream_id, text)
            else:
                await self._send_text(stream_id, text)
            return True, text, True

    @Command(
        "ongeki_task_list",
        description="查看随机任务列表",
        pattern=r"^/任务列表\s*$",
    )
    async def handle_task_list(
        self,
        stream_id: str = "",
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        user_id = self._user_id(kwargs)
        if not self.config.task.enabled:
            text = "随机任务功能未启用"
            await self._send_text(stream_id, text)
            return True, text, True
        if self._db is None:
            text = "插件未就绪，请稍后重试"
            await self._send_text(stream_id, text)
            return True, text, True
        today = GachaDatabase.current_date_str(
            self.config.economy.tz_offset_hours
        )
        normal_used = self._db.get_task_quota(
            user_id,
            task_kind="normal",
            task_date=today,
        )
        challenge_used = self._db.get_task_quota(
            user_id,
            task_kind="challenge",
            task_date=today,
        )
        advanced_used = self._db.get_task_quota(
            user_id,
            task_kind="advanced",
            task_date=today,
        )
        lines = [
            "【随机任务】",
            f"普通 {max(self.config.task.normal_count - normal_used, 0)}"
            f"/{self.config.task.normal_count}"
            f"｜挑战 {max(self.config.task.challenge_count - challenge_used, 0)}"
            f"/{self.config.task.challenge_count}"
            f"｜高级挑战 {max(self.config.task.advanced_count - advanced_used, 0)}"
            f"/{self.config.task.advanced_count}",
        ]
        status_labels = {
            "active": "待完成",
            "submitted": "待审核",
            "approved": "已通过",
            "rejected": "已拒绝",
            "reset": "已重置",
            "expired": "已过期",
        }
        tasks = [
            task
            for task in self._db.list_tasks(user_id, limit=100)
            if task.task_kind == "ultimate" or task.task_date == today
        ][:30]
        if tasks:
            lines.append("")
            for task in tasks:
                game_name = GAME_LABELS.get(task.game, task.game)
                status = status_labels.get(task.status, task.status)
                lines.append(
                    f"#{task.id} [{game_name}] {self._ellipsize(task.song_title, 16)}"
                    f"｜{task.requirement_text}｜{status}"
                )
        else:
            lines.append("")
            lines.append("暂无任务。发送 /接任务 普通 领取任务")
        lines.append("接取：/接任务 普通|挑战|高级挑战｜提交：发送成绩图及 /任务完成 <ID>")
        text = "\n".join(lines)
        await self._send_text(stream_id, text, title="音击抽卡模拟器 · 任务列表")
        return True, text, True

    @Command(
        "ongeki_task_submit",
        description="提交任务完成照片",
        pattern=(
            r"^/任务完成\s+"
            r"(?P<task_id>\d+)(?:\s+(?P<note>.+))?\s*$"
        ),
    )
    async def handle_task_submit(
        self,
        stream_id: str = "",
        matched_groups: dict = None,
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        user_id = self._user_id(kwargs)
        groups = matched_groups or {}
        try:
            task_id = int(str(groups.get("task_id") or "0"))
        except ValueError:
            task_id = 0
        note = str(groups.get("note") or "").strip()
        if task_id <= 0:
            text = "用法：发送成绩图及 /任务完成 <ID>"
            await self._send_text(stream_id, text)
            return True, text, True
        if self.config.task.require_photo and not self._has_photo(kwargs):
            text = "请在发送 /任务完成 <ID> 时附上成绩图"
            await self._send_text(stream_id, text)
            return True, text, True
        if self._db is None:
            text = "插件未就绪，请稍后重试"
            await self._send_text(stream_id, text)
            return True, text, True
        task = self._db.get_task(task_id)
        if task is None or task.qq_id != user_id:
            text = "未找到该任务或不属于你"
            await self._send_text(stream_id, text)
            return True, text, True
        receipt = self._db.submit_task(task_id, user_id, note=note)
        if not receipt.success:
            text = receipt.error or "提交失败"
            await self._send_text(stream_id, text)
            return True, text, True
        lines = [
            f"任务 #{task_id} 已提交，等待管理员审核｜账号 {user_id}",
            f"{GAME_LABELS.get(task.game, task.game)}"
            f"｜{self._ellipsize(task.song_title, 20)}"
            f"｜{task.requirement_text}",
        ]
        if note:
            lines.append(f"备注 {note}")
        lines.append(f"管理员 /任务审核 {task_id} S|SS|SSS|SSS+")
        text = "\n".join(lines)
        await self._send_text(stream_id, text)
        return True, text, True

    @Command(
        "ongeki_task_review",
        description="管理员审核随机任务",
        pattern=(
            r"^/任务审核\s+(?P<task_id>\d+)\s+"
            r"(?P<grade>普通|S|SS|SSS|SSS\+|SSS＋|拒绝|通过)"
            r"(?:\s+(?P<note>.+))?\s*$"
        ),
    )
    async def handle_task_review(
        self,
        stream_id: str = "",
        matched_groups: dict = None,
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        user_id = self._user_id(kwargs)
        if not self._is_admin(user_id):
            text = "仅管理员可审核任务"
            await self._send_text(stream_id, text)
            return True, text, True
        groups = matched_groups or {}
        try:
            task_id = int(str(groups.get("task_id") or "0"))
        except ValueError:
            task_id = 0
        grade = self._normalize_grade(str(groups.get("grade") or ""))
        note = str(groups.get("note") or "").strip()
        if task_id <= 0 or self._db is None:
            text = "用法：/任务审核 <任务ID> <普通|S|SS|SSS|SSS+|拒绝> [备注]"
            await self._send_text(stream_id, text)
            return True, text, True
        task = self._db.get_task(task_id)
        if task is None:
            text = "任务不存在｜可用 /任务审核列表 查看待审核任务"
            await self._send_text(stream_id, text)
            return True, text, True
        if task.task_kind == "ultimate":
            text = "终极任务请用 /终极完成"
            await self._send_text(stream_id, text)
            return True, text, True
        if grade == "拒绝":
            receipt = self._db.reject_task(task_id, user_id, note=note)
            text = "任务已拒绝" if receipt.success else (receipt.error or "操作失败")
            await self._send_text(stream_id, text)
            return True, text, True
        if task.task_kind == "normal":
            if grade not in {"普通", "S"}:
                text = "普通任务请使用“普通”档审核"
                await self._send_text(stream_id, text)
                return True, text, True
            grade = "普通"
        elif grade not in {"S", "SS", "SSS", "SSS+"}:
            label = "高级挑战" if task.task_kind == "advanced" else "挑战"
            text = f"{label}任务请选择 S / SS / SSS / SSS+"
            await self._send_text(stream_id, text)
            return True, text, True
        reward = self._task_reward(task.task_kind, grade)
        receipt = self._db.approve_task(
            task_id,
            user_id,
            grade=grade,
            reward=reward,
        )
        if receipt.success:
            text = (
                f"#{task_id} 审核通过 {grade}"
                f"｜已发放 {reward} 点（当前 {receipt.points}）"
            )
            if receipt.bloom_tickets:
                text += f"｜解花券 +{receipt.bloom_tickets}"
            elif receipt.cooldown_text:
                text += f"｜{receipt.cooldown_text}"
            if self.config.growth.enabled:
                text += _growth_reward_text(
                    receipt.medium_gifts, receipt.large_gifts, receipt.growth_fragments
                )
                await self._send_item_gain_card(
                    stream_id,
                    task.qq_id,
                    {
                        "gift_medium": receipt.medium_gifts,
                        "gift_large": receipt.large_gifts,
                        "flower_fragment": receipt.growth_fragments,
                        "bloom_ticket": receipt.bloom_tickets,
                    },
                    title="任务奖励物品",
                    subtitle=f"任务 #{task_id} · {grade} 档",
                )
        else:
            text = receipt.error or "审核失败"
        await self._send_text(stream_id, text)
        return True, text, True

    @Command(
        "ongeki_task_pending",
        description="管理员查看待审核任务",
        pattern=r"^/任务审核列表\s*$",
    )
    async def handle_task_pending(
        self,
        stream_id: str = "",
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        user_id = self._user_id(kwargs)
        if not self._is_admin(user_id) or self._db is None:
            text = "仅管理员可操作（或插件未就绪）"
            await self._send_text(stream_id, text)
            return True, text, True
        tasks = self._db.list_pending_tasks()
        if not tasks:
            text = "暂无待审核任务"
        else:
            lines = ["【待审核任务】"]
            for task in tasks:
                lines.append(
                    f"#{task.id} | {GAME_LABELS.get(task.game, task.game)}"
                    f"｜{self._ellipsize(task.song_title, 16)}"
                    f"｜{task.requirement_text}"
                    f"｜接取人 {task.qq_id}"
                )
            text = "\n".join(lines)
        await self._send_text(stream_id, text, title="音击抽卡模拟器 · 待审核任务")
        return True, text, True

    @Command(
        "ongeki_ultimate_complete",
        description="管理员确认终极任务完成",
        pattern=(
            r"^/终极完成\s+"
            r"(?:(?P<target_at>@\S+)|(?P<target_id>\d+))\s+"
            r"(?P<task_id>\d+)(?:\s+(?P<note>.+))?\s*$"
        ),
    )
    async def handle_ultimate_complete(
        self,
        stream_id: str = "",
        matched_groups: dict = None,
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        user_id = self._user_id(kwargs)
        if not self._is_admin(user_id) or self._db is None:
            text = "仅管理员可确认终极任务"
            await self._send_text(stream_id, text)
            return True, text, True
        groups = matched_groups or {}
        target_id = self._resolve_task_target(kwargs, groups)
        try:
            task_id = int(str(groups.get("task_id") or "0"))
        except ValueError:
            task_id = 0
        note = str(groups.get("note") or "").strip()
        if not target_id or task_id <= 0:
            text = "用法 /终极完成 <QQ号> <任务ID>（ID 见 /任务列表）"
            await self._send_text(stream_id, text)
            return True, text, True
        task = self._db.get_task(task_id)
        if task is None:
            text = "任务不存在｜可用 /任务审核列表 查看待审核任务"
            await self._send_text(stream_id, text)
            return True, text, True
        if task.task_kind != "ultimate" or task.qq_id != target_id:
            text = (
                "该任务不是该用户的终极任务"
                "（/终极完成 <QQ号> <任务ID>，ID 见 /任务列表）"
            )
            await self._send_text(stream_id, text)
            return True, text, True
        receipt = self._db.complete_ultimate(
            task_id,
            user_id,
            reward=self.config.task.ultimate_reward,
        )
        text = (
            f"终极任务 #{task_id} 完成"
            f"｜已发放 {self.config.task.ultimate_reward} 点"
            f"（当前 {receipt.points}）"
            if receipt.success
            else (receipt.error or "确认失败")
        )
        if receipt.success and self.config.growth.enabled:
            text += _growth_reward_text(
                receipt.medium_gifts, receipt.large_gifts, receipt.growth_fragments
            )
            await self._send_item_gain_card(
                stream_id,
                target_id,
                {
                    "gift_medium": receipt.medium_gifts,
                    "gift_large": receipt.large_gifts,
                    "flower_fragment": receipt.growth_fragments,
                },
                title="终极任务奖励物品",
                subtitle=f"任务 #{task_id}",
            )
        await self._send_text(stream_id, text)
        return True, text, True

    @Command(
        "ongeki_task_reset",
        description="管理员隐藏任务重置",
        pattern=r"^/任务重置\s+(?P<task_id>\d+)(?:\s+(?P<note>.+))?\s*$",
    )
    async def handle_task_reset(
        self,
        stream_id: str = "",
        matched_groups: dict = None,
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        user_id = self._user_id(kwargs)
        if not self._is_admin(user_id) or self._db is None:
            text = "仅管理员可重置任务"
            await self._send_text(stream_id, text)
            return True, text, True
        groups = matched_groups or {}
        try:
            task_id = int(str(groups.get("task_id") or "0"))
        except ValueError:
            task_id = 0
        note = str(groups.get("note") or "").strip()
        if task_id <= 0:
            text = "用法：/任务重置 <任务ID> [备注]"
            await self._send_text(stream_id, text)
            return True, text, True
        today = GachaDatabase.current_date_str(
            self.config.economy.tz_offset_hours
        )
        receipt = self._db.reset_task(
            task_id,
            user_id,
            note=note,
            today=today,
        )
        text = (
            f"任务 #{task_id} 已重置，次数已返还"
            if receipt.success
            else (receipt.error or "重置失败")
        )
        await self._send_text(stream_id, text)
        return True, text, True

    @Command(
        "ongeki_task_cleanup",
        description="管理员清理已结束的历史任务",
        pattern=r"^/任务清理(?:\s+(?P<days>\d+))?\s*$",
    )
    async def handle_task_cleanup(
        self,
        stream_id: str = "",
        matched_groups: dict = None,
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        user_id = self._user_id(kwargs)
        if not self._is_admin(user_id) or self._db is None:
            text = "仅管理员可操作（或插件未就绪）"
            await self._send_text(stream_id, text)
            return True, text, True
        groups = matched_groups or {}
        raw_days = str(groups.get("days") or "").strip()
        if raw_days.isdigit() and int(raw_days) >= 0:
            retention_days = int(raw_days)
        else:
            retention_days = self.config.task.task_history_retention_days
        today = GachaDatabase.current_date_str(
            self.config.economy.tz_offset_hours
        )
        cleaned_tasks = self._db.cleanup_task_history(
            today,
            retention_days=retention_days,
        )
        cleaned_quota = self._db.cleanup_daily_task_quota(
            today,
            retention_days=retention_days,
        )
        text = (
            f"已清理 {cleaned_tasks} 条历史任务、"
            f"{cleaned_quota} 条每日配额（待审核保留）"
            
        )
        await self._send_text(stream_id, text)
        return True, text, True

    def _resolve_task_target(self, kwargs: dict[str, Any], groups: dict) -> str:
        target_id = str(groups.get("target_id") or "").strip()
        if target_id.isdigit():
            return target_id
        target_at = str(groups.get("target_at") or "").strip()
        if target_at:
            resolved = self._extract_at_target_id(kwargs)
            if resolved:
                return resolved
            literal = target_at[1:].strip()
            if literal.isdigit():
                return literal
        text = str(kwargs.get("text") or "")
        match = re.search(r"@([^\s]+)\s+(\d+)", text)
        if match:
            resolved = self._extract_at_target_id(kwargs)
            if resolved:
                return resolved
        match = re.search(r"(?<!\d)(\d+)\s+(\d+)(?!\d)", text)
        return match.group(2) if match is not None else ""

    def _task_game_from_kwargs(self, kwargs: dict[str, Any]) -> str | None:
        groups = kwargs.get("matched_groups")
        raw = ""
        if isinstance(groups, dict):
            raw = str(groups.get("game") or "").strip().lower()
        if raw:
            aliases = {
                "音击": "ongeki",
                "ongeki": "ongeki",
                "舞萌": "maimai",
                "舞萌dx": "maimai",
                "maimai": "maimai",
                "中二": "chunithm",
                "中二节奏": "chunithm",
                "chunithm": "chunithm",
            }
            return aliases.get(raw)
        text = str(kwargs.get("text") or "").lower()
        for keyword, game in (
            ("音击", "ongeki"),
            ("ongeki", "ongeki"),
            ("舞萌", "maimai"),
            ("maimai", "maimai"),
            ("中二", "chunithm"),
            ("chunithm", "chunithm"),
        ):
            if keyword in text:
                return game
        return None
