"""ONGEKI 模拟抽卡插件入口。"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from maibot_sdk import Command, MaiBotPlugin

from .config_model import OngekiGachaPluginConfig
from .gacha_core import CardCollection, CardPool, RARITY_ORDER, load_cards, rarity_display
from .gacha_db import GachaDatabase
from .gacha_render import GachaRenderer, RenderCard


logger = logging.getLogger(__name__)

HELP_TEXT = (
    "ONGEKI 模拟抽卡（V1 全卡大混池）\n"
    "/签到 每日领取 400～800 点\n"
    "/抽卡 1 单抽（50 点）\n"
    "/抽卡 5 五连（250 点，含 SR 以上保底）\n"
    "/抽卡 11 十一连（500 点，含 SR 以上保底）\n"
    "/点数 查看余额\n"
    "/卡册 查看收藏进度\n"
    "/概率 查看当前模拟权重\n"
    "/帮助 显示本帮助\n"
    "所有数据均为本地娱乐模拟，不代表 SEGA 官方概率。"
)


class OngekiGachaPlugin(MaiBotPlugin):
    """ONGEKI 全卡大混池模拟抽卡插件。"""

    config_model = OngekiGachaPluginConfig

    def __init__(self) -> None:
        super().__init__()
        self._lock: asyncio.Lock = asyncio.Lock()
        self._cards: CardCollection | None = None
        self._pool: CardPool | None = None
        self._db: GachaDatabase | None = None
        self._renderer: GachaRenderer | None = None

    async def on_load(self) -> None:
        """加载卡牌、初始化数据库和渲染器。"""
        self._initialize()
        self.ctx.logger.info("ONGEKI 模拟抽卡插件已加载")

    async def on_unload(self) -> None:
        """释放数据库连接。"""
        if self._db is not None:
            self._db.close()
        self._db = None

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        """配置热更新后提示重新加载，避免路径变化后仍使用旧资源。"""
        del config_data
        del version
        if scope == "self":
            self.ctx.logger.info("ONGEKI 模拟抽卡配置已更新，如需修改素材路径请重新加载插件")

    def _initialize(self) -> None:
        config = self.config
        cards_dir = Path(config.assets.cards_dir).expanduser()
        card_info_path = Path(config.assets.card_info_json).expanduser()
        cards = load_cards(card_info_path)
        pool = CardPool(
            cards,
            weight_n=config.pool.weight_n,
            weight_r=config.pool.weight_r,
            weight_sr=config.pool.weight_sr,
            weight_sr_plus=config.pool.weight_sr_plus,
            weight_ssr=config.pool.weight_ssr,
        )
        db_path = self.ctx.paths.data_dir / "ongeki_gacha.db"
        database = GachaDatabase(db_path)
        database.open()
        renderer = GachaRenderer(cards_dir, Path(__file__).resolve().parent / "assets" / "ui")
        self._cards = cards
        self._pool = pool
        self._db = database
        self._renderer = renderer

    @staticmethod
    def _user_id(kwargs: dict[str, Any]) -> str:
        user_id = str(kwargs.get("user_id") or kwargs.get("sender_id") or "").strip()
        if user_id:
            return user_id
        if kwargs.get("is_local_operator"):
            return "local-operator"
        return "unknown"

    @staticmethod
    def _parse_count(kwargs: dict[str, Any]) -> int:
        groups = kwargs.get("matched_groups")
        if isinstance(groups, dict):
            raw_count = str(groups.get("count") or "").strip()
            if raw_count in {"1", "5", "11"}:
                return int(raw_count)
        text = str(kwargs.get("text") or "")
        match = re.search(r"(?<!\d)(11|5|1)(?!\d)", text)
        return int(match.group(1)) if match is not None else 1

    def _cost(self, count: int) -> int:
        config = self.config.economy
        return {
            1: config.cost_1,
            5: config.cost_5,
            11: config.cost_11,
        }.get(count, 0)

    async def _send_text(self, stream_id: str, text: str) -> None:
        await self.ctx.send.text(text, stream_id)

    @staticmethod
    def _rare_summary(cards: list[CardInfo]) -> str:
        counts = Counter(rarity_display(card.rarity) for card in cards)
        parts = []
        for label in ("SSR", "SR+", "SR", "R", "N"):
            if counts[label]:
                parts.append(f"{label}×{counts[label]}")
        return " · ".join(parts) if parts else "无"

    # ==================== 命令 ====================

    @Command(
        "ongeki_draw",
        description="ONGEKI 模拟抽卡，支持 1/5/11 连",
        pattern=r"^/抽卡(?:\s+(?P<count>11|5|1))?\s*$",
        aliases=["/og抽卡", "/og 抽卡", "/gacha"],
    )
    async def handle_draw(self, stream_id: str = "", **kwargs: dict[str, Any]) -> tuple[bool, str, bool]:
        """执行抽卡并发送结果图。"""
        user_id = self._user_id(kwargs)
        count = self._parse_count(kwargs)
        cost = self._cost(count)
        if cost <= 0:
            text = "抽卡数量只能为 1、5 或 11"
            await self._send_text(stream_id, text)
            return True, text, True

        async with self._lock:
            if self._db is None or self._pool is None or self._renderer is None or self._cards is None:
                text = "插件尚未初始化完成，请检查日志"
                await self._send_text(stream_id, text)
                return True, text, True

            player = self._db.get_player(user_id)
            if player.points < cost:
                text = f"点数不足（需要 {cost}，当前 {player.points}）。请先 /签到"
                await self._send_text(stream_id, text)
                return True, text, True

            drawn_cards = self._pool.draw(count)
            receipt = self._db.commit_draw(
                user_id,
                [(card.id, card.rarity) for card in drawn_cards],
                cost=cost,
            )
            if not receipt.success:
                text = receipt.error or "抽卡失败"
                await self._send_text(stream_id, text)
                return True, text, True

            states = [
                RenderCard(
                    card=card,
                    copies=commitment.copies,
                    is_new=commitment.is_new,
                    is_kaika=commitment.is_kaika,
                    is_cho_kaika=commitment.is_cho_kaika,
                )
                for card, commitment in zip(drawn_cards, receipt.commitments, strict=True)
            ]
            summary = "\n".join(
                [
                    f"本次抽取：{count}连",
                    f"稀有度：{self._rare_summary(drawn_cards)}",
                    f"新卡：{sum(1 for item in receipt.commitments if item.is_new)} 张",
                    f"剩余点数：{receipt.points}",
                ]
            )
            output_path = self.ctx.paths.runtime_dir / f"ongeki_draw_{count}_{time_ns()}.png"
            try:
                image_bytes = self._renderer.render(states, output_path)
                image_base64 = base64.b64encode(image_bytes).decode("ascii")
                await self.ctx.send.image(image_base64, stream_id)
            except Exception as exc:
                self.ctx.logger.error("抽卡图片发送失败: %s", exc, exc_info=True)

        await self._send_text(stream_id, summary)
        return True, summary, True

    @Command("ongeki_checkin", description="每日签到领取点数", pattern=r"^/签到\s*$", aliases=["/og签到", "/og 签到", "/打卡"])
    async def handle_checkin(self, stream_id: str = "", **kwargs: dict[str, Any]) -> tuple[bool, str, bool]:
        """执行每日签到。"""
        user_id = self._user_id(kwargs)
        config = self.config.economy
        async with self._lock:
            if self._db is None:
                text = "插件尚未初始化完成，请检查日志"
                await self._send_text(stream_id, text)
                return True, text, True
            receipt = self._db.daily_checkin(
                user_id,
                min_reward=config.min_reward,
                max_reward=config.max_reward,
                tz_offset_hours=config.tz_offset_hours,
            )
        if receipt.success:
            text = f"签到成功！获得 {receipt.reward} 点，当前点数：{receipt.points}"
        else:
            text = receipt.error or "签到失败"
        await self._send_text(stream_id, text)
        return True, text, True

    @Command("ongeki_points", description="查看点数余额", pattern=r"^/(?:点数|余额)\s*$", aliases=["/og点数", "/og 点数", "/og余额"])
    async def handle_points(self, stream_id: str = "", **kwargs: dict[str, Any]) -> tuple[bool, str, bool]:
        """查看玩家点数。"""
        user_id = self._user_id(kwargs)
        async with self._lock:
            if self._db is None:
                text = "插件尚未初始化完成，请检查日志"
                await self._send_text(stream_id, text)
                return True, text, True
            player = self._db.get_player(user_id)
        text = f"当前点数：{player.points}｜累计签到 {player.total_checkins} 次｜累计抽卡 {player.total_pulls} 次"
        await self._send_text(stream_id, text)
        return True, text, True

    @Command("ongeki_inventory", description="查看卡册进度", pattern=r"^/(?:卡册|图鉴)\s*$", aliases=["/og卡册", "/og 卡册"])
    async def handle_inventory(self, stream_id: str = "", **kwargs: dict[str, Any]) -> tuple[bool, str, bool]:
        """查看玩家卡册。"""
        user_id = self._user_id(kwargs)
        async with self._lock:
            if self._db is None or self._cards is None:
                text = "插件尚未初始化完成，请检查日志"
                await self._send_text(stream_id, text)
                return True, text, True
            inventory = self._db.get_inventory(user_id)
            player = self._db.get_player(user_id)
        total_cards = len(self._cards.cards)
        unique = len(inventory)
        if unique == 0:
            text = "卡册还是空的，先 /签到 再 /抽卡 吧！"
            await self._send_text(stream_id, text)
            return True, text, True

        rarity_counts: Counter[str] = Counter()
        total_copies = 0
        for entry in inventory:
            total_copies += entry.copies
            card = self._cards.by_id.get(entry.card_id)
            if card is not None:
                rarity_counts[rarity_display(card.rarity)] += 1

        lines = [
            f"卡册进度：已拥有 {unique}/{total_cards} 张（重复总计 {total_copies - unique} 张）",
            "稀有度：" + " · ".join(f"{label}×{rarity_counts[label]}" for label in ("SSR", "SR+", "SR", "R", "N") if rarity_counts[label]),
            f"当前点数：{player.points}",
        ]

        entry_by_rarity = sorted(
            inventory,
            key=lambda entry: (
                RARITY_ORDER.get(self._cards.by_id.get(entry.card_id).rarity if self._cards.by_id.get(entry.card_id) else "", 99),
                entry.card_id,
            ),
        )
        preview_lines = []
        for entry in entry_by_rarity[:12]:
            card = self._cards.by_id.get(entry.card_id)
            if card is None:
                continue
            short_name = card.name[:36]
            preview_lines.append(f"{rarity_display(card.rarity)} {short_name} ×{entry.copies}")
        if preview_lines:
            lines.append("部分收藏：")
            lines.extend(preview_lines)
        text = "\n".join(lines)
        await self._send_text(stream_id, text)
        return True, text, True

    @Command("ongeki_odds", description="查看模拟概率", pattern=r"^/(?:概率|抽卡概率)\s*$", aliases=["/og概率", "/og 概率"])
    async def handle_odds(self, stream_id: str = "", **kwargs: dict[str, Any]) -> tuple[bool, str, bool]:
        """显示当前模拟权重。"""
        del kwargs
        config = self.config
        if self._pool is None:
            text = "插件尚未初始化完成，请检查日志"
            await self._send_text(stream_id, text)
            return True, text, True
        lines = [
            "V1 全卡大混池（本地娱乐值，非官方概率）",
        ]
        weights = self._pool.rarity_weights
        total_weight = sum(weight for _, weight in weights)
        for rarity, weight in weights:
            percentage = weight / total_weight * 100
            lines.append(f"{rarity_display(rarity)}：{weight}（约 {percentage:.1f}%）")
        lines.append(f"消耗：1连 {config.economy.cost_1} / 5连 {config.economy.cost_5} / 11连 {config.economy.cost_11} 点")
        lines.append("保底：5连、11连至少 1 张 SR / SR+ / SSR")
        text = "\n".join(lines)
        await self._send_text(stream_id, text)
        return True, text, True

    @Command("ongeki_help", description="显示插件帮助", pattern=r"^/(?:帮助|on帮助)\s*$", aliases=["/og帮助", "/og 帮助"])
    async def handle_help(self, stream_id: str = "", **kwargs: dict[str, Any]) -> tuple[bool, str, bool]:
        """显示帮助信息。"""
        del kwargs
        await self._send_text(stream_id, HELP_TEXT)
        return True, HELP_TEXT, True


def time_ns() -> int:
    """返回当前纳秒时间戳，避免额外导入 time。"""
    import time

    return time.time_ns()


def create_plugin() -> OngekiGachaPlugin:
    """创建插件实例。"""
    return OngekiGachaPlugin()
