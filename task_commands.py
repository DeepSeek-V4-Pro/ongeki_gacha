# -*- coding: utf-8 -*-
"""随机任务命令层。"""

from __future__ import annotations

import re
from typing import Any

from maibot_sdk import Command

from .gacha_db import GachaDatabase
from .task_catalog import GAME_LABELS, pick_random_task


class TaskCommandsMixin:
    """随机任务命令。"""

    @Command(
        "ongeki_task_accept",
        description="接取音游随机任务",
    pattern=r"^/(?:接任务|领取任务|任务)\s+(?P<kind>普通|挑战|终极)(?:\s+(?P<game>\S+))?\s*$",
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
                text = "插件尚未初始化完成，请检查日志"
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
                if progress.finished:
                    text = "终极任务已完成，无法再次接取"
                    await self._send_text(stream_id, text)
                    return True, text, True
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
                ultimate_min_level=self.config.task.ultimate_min_level,
            )
            if selection is None:
                if task_kind == "ultimate":
                    reason = (
                        "该游戏终极候选曲目已全部完成"
                        if game
                        else "终极候选曲目已全部完成"
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
        pattern=r"^/(?:任务列表|我的任务)\s*$",
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
            text = "插件尚未初始化完成，请检查日志"
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
        lines = [
            "【随机任务】",
            f"今日普通任务剩余：{max(self.config.task.normal_count - normal_used, 0)}/{self.config.task.normal_count}",
            f"今日挑战任务剩余：{max(self.config.task.challenge_count - challenge_used, 0)}/{self.config.task.challenge_count}",
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
                    f"#{task.id} [{game_name}] {task.song_title} "
                    f"| {task.requirement_text} | {status}"
                )
        else:
            lines.append("")
            lines.append("暂无任务，发送 /接任务 普通 领取")
        await self._send_lines(
            stream_id,
            lines,
            title="音击抽卡模拟器 · 任务列表",
        )
        text = "\n".join(lines)
        return True, text, True

    @Command(
        "ongeki_task_submit",
        description="提交任务完成照片",
        pattern=(
            r"^/(?:任务完成|提交任务|完成任务)\s+"
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
            text = "用法：/任务完成 <任务ID>，并附带成绩照片"
            await self._send_text(stream_id, text)
            return True, text, True
        if self.config.task.require_photo and not self._has_photo(kwargs):
            text = "请随任务完成指令一起发送成绩照片"
            await self._send_text(stream_id, text)
            return True, text, True
        if self._db is None:
            text = "插件尚未初始化完成，请检查日志"
            await self._send_text(stream_id, text)
            return True, text, True
        task = self._db.get_task(task_id)
        if task is None or task.qq_id != user_id:
            text = "未找到该任务，或该任务不属于你"
            await self._send_text(stream_id, text)
            return True, text, True
        receipt = self._db.submit_task(task_id, user_id, note=note)
        if not receipt.success:
            text = receipt.error or "提交失败"
            await self._send_text(stream_id, text)
            return True, text, True
        lines = [
            f"任务 #{task_id} 已提交，请管理员审核。",
            f"接取人：{user_id}",
            f"游戏：{GAME_LABELS.get(task.game, task.game)}",
            f"曲目：{task.song_title} — {task.artist}",
            f"要求：{task.requirement_text}",
        ]
        if note:
            lines.append(f"备注：{note}")
        text = "\n".join(lines)
        await self._send_text(stream_id, text)
        return True, text, True

    @Command(
        "ongeki_task_review",
        description="管理员审核随机任务",
        pattern=(
            r"^/(?:任务审核|审核任务)\s+(?P<task_id>\d+)\s+"
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
            text = "你不是管理员，无法审核任务"
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
            text = "任务不存在"
            await self._send_text(stream_id, text)
            return True, text, True
        if task.task_kind == "ultimate":
            text = "终极任务请使用 /终极完成"
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
            text = "挑战任务请选择 S / SS / SSS / SSS+"
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
                f"任务 #{task_id} 审核通过：{grade} 档，"
                f"已发放 {reward} 点，当前点数 {receipt.points}"
            )
        else:
            text = receipt.error or "审核失败"
        await self._send_text(stream_id, text)
        return True, text, True

    @Command(
        "ongeki_task_pending",
        description="管理员查看待审核任务",
        pattern=r"^/(?:任务审核列表|待审任务|待审核列表)\s*$",
    )
    async def handle_task_pending(
        self,
        stream_id: str = "",
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        user_id = self._user_id(kwargs)
        if not self._is_admin(user_id) or self._db is None:
            text = "你不是管理员，或插件尚未初始化完成"
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
                    f" | {task.song_title} | {task.requirement_text}"
                    f" | 接取人 {task.qq_id}"
                )
            text = "\n".join(lines)
        await self._send_lines(
            stream_id,
            text,
            title="音击抽卡模拟器 · 待审核任务",
        )
        return True, text, True

    @Command(
        "ongeki_ultimate_complete",
        description="管理员确认终极任务完成",
        pattern=(
            r"^/(?:终极完成|终极确认|终极审核)\s+"
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
            text = "你不是管理员，无法确认终极任务"
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
            text = "用法：/终极完成 <用户> <任务ID> [备注]"
            await self._send_text(stream_id, text)
            return True, text, True
        task = self._db.get_task(task_id)
        if task is None:
            text = "任务不存在"
            await self._send_text(stream_id, text)
            return True, text, True
        if task.task_kind != "ultimate" or task.qq_id != target_id:
            text = "该任务不是目标用户的终极任务"
            await self._send_text(stream_id, text)
            return True, text, True
        receipt = self._db.complete_ultimate(
            task_id,
            user_id,
            reward=self.config.task.ultimate_reward,
            ultimate_total=1,
        )
        text = (
            f"终极任务 #{task_id} 已完成，已发放 "
            f"{self.config.task.ultimate_reward} 点，当前点数 {receipt.points}"
            if receipt.success
            else (receipt.error or "确认失败")
        )
        await self._send_text(stream_id, text)
        return True, text, True

    @Command(
        "ongeki_task_reset",
        description="管理员隐藏任务重置",
        pattern=r"^/(?:任务重置|重置任务)\s+(?P<task_id>\d+)(?:\s+(?P<note>.+))?\s*$",
    )
    async def handle_task_reset(
        self,
        stream_id: str = "",
        matched_groups: dict = None,
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        user_id = self._user_id(kwargs)
        if not self._is_admin(user_id) or self._db is None:
            text = "你不是管理员，无法重置任务"
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
            "任务已重置，用户可重新接取"
            if receipt.success
            else (receipt.error or "重置失败")
        )
        await self._send_text(stream_id, text)
        return True, text, True

    @Command(
        "ongeki_task_cleanup",
        description="管理员清理已结束的历史任务",
        pattern=r"^/(?:任务清理|清理任务)(?:\s+(?P<days>\d+))?\s*$",
    )
    async def handle_task_cleanup(
        self,
        stream_id: str = "",
        matched_groups: dict = None,
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        user_id = self._user_id(kwargs)
        if not self._is_admin(user_id) or self._db is None:
            text = "你不是管理员，或插件尚未初始化完成"
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
            f"已清理 {cleaned_tasks} 条已结束任务、"
            f"{cleaned_quota} 条旧每日配额。"
            "待审核任务不会被清理。"
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
