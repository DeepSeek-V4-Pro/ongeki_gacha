"""ONGEKI 模拟抽卡插件入口。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from maibot_sdk import Command, MaiBotPlugin

import asyncio
import base64
import json
import logging
import re
import time

from .config_model import OngekiGachaPluginConfig
from .gacha_core import CardCollection, CardPool, RARITY_ORDER, load_cards, rarity_display
from .gacha_db import GachaDatabase
from .gacha_render import GachaRenderer, RenderCard


logger = logging.getLogger(__name__)
RENDER_CACHE_TTL_SECONDS = 24 * 60 * 60

HELP_TEXT = (
    "音击抽卡模拟器（MaiBot 本地娱乐插件）\n"
    "/签到 每日随机领取 400～800 点，可能有欧皇彩蛋\n"
    "/抽卡 1 单抽（50 点）\n"
    "/抽卡 5 五连（250 点，每用户每周首次含 SR 或以上保底）\n"
    "/抽卡 11 十一连（500 点，含 SR 或以上保底）\n"
    "/点数 查看余额\n"
    "/卡册 查看收藏进度\n"
    "/卡图 <ID> 查看已拥有卡牌的高清大图\n"
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
        self._cleanup_task: asyncio.Task | None = None

    async def on_load(self) -> None:
        """加载卡牌、初始化数据库和渲染器。"""
        self._initialize()
        self._cleanup_render_cache()
        self._cleanup_task = asyncio.create_task(self._daily_render_cache_cleanup_loop())
        self.ctx.logger.info("ONGEKI 模拟抽卡插件已加载")

    async def on_unload(self) -> None:
        """释放数据库连接。"""
        if self._db is not None:
            self._db.close()
        self._db = None
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        """配置热更新后提示重新加载，避免路径变化后仍使用旧资源。"""
        del config_data
        del version
        if scope == "self":
            self.ctx.logger.info("ONGEKI 模拟抽卡配置已更新，如需修改素材路径请重新加载插件")

    def _initialize(self) -> None:
        config = self.config
        plugin_root = Path(__file__).resolve().parent
        configured_cards_dir = Path(config.assets.cards_dir).expanduser()
        configured_card_info_path = Path(config.assets.card_info_json).expanduser()
        if not configured_cards_dir.is_absolute():
            configured_cards_dir = plugin_root / configured_cards_dir
        if not configured_card_info_path.is_absolute():
            configured_card_info_path = plugin_root / configured_card_info_path
        configured_cards_dir = configured_cards_dir.resolve()
        configured_card_info_path = configured_card_info_path.resolve()

        default_cards_dir = (plugin_root / "assets" / "card_data").resolve()
        default_card_info_path = (default_cards_dir / "card_info_merged.json").resolve()

        cards_dir, card_info_path = self._resolve_card_data_paths(
            configured_cards_dir,
            configured_card_info_path,
            default_cards_dir,
            default_card_info_path,
        )
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

    @classmethod
    def _resolve_card_data_paths(
        cls,
        configured_cards_dir: Path,
        configured_card_info_path: Path,
        default_cards_dir: Path,
        default_card_info_path: Path,
    ) -> tuple[Path, Path]:
        """Choose default card_data, or the user's manually configured path."""
        configured_ready = cls._card_data_quick_ready(
            configured_cards_dir,
            configured_card_info_path,
        )
        configured_is_manual = configured_cards_dir != default_cards_dir

        if configured_ready and configured_is_manual:
            logger.info("使用手动配置的卡牌数据路径: %s", configured_cards_dir)
            return configured_cards_dir, configured_card_info_path

        default_ready = cls._card_data_quick_ready(
            default_cards_dir,
            default_card_info_path,
        )
        if default_ready:
            if configured_ready and not configured_is_manual:
                logger.info("使用默认卡牌数据目录: %s", default_cards_dir)
            elif configured_is_manual:
                logger.warning(
                    "手动配置的卡牌数据不可用，回退到默认目录: %s",
                    default_cards_dir,
                )
            return default_cards_dir, default_card_info_path

        if configured_ready:
            logger.info("使用可用的手动配置卡牌数据路径: %s", configured_cards_dir)
            return configured_cards_dir, configured_card_info_path

        raise FileNotFoundError(
            "卡牌数据不可用。请先运行 sync_card_data.py，"
            "或在配置 assets.cards_dir / assets.card_info_json 中填写有效绝对路径。"
        )

    @staticmethod
    def _card_data_quick_ready(cards_dir: Path, card_info_path: Path) -> bool:
        """Quick check: manifest files or JSON-referenced PNGs all exist."""
        if not cards_dir.is_dir() or not card_info_path.is_file():
            return False
        manifest_path = cards_dir / "card_data_manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                files = manifest.get("files") or []
                if files and all(
                    (cards_dir / str(item.get("name", ""))).is_file()
                    for item in files
                ):
                    return True
            except (OSError, ValueError, TypeError):
                pass
        try:
            rows = json.loads(card_info_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return False
        for row in rows:
            card_id = row.get("id")
            image_file = str(
                row.get("imageFile")
                or (f"ui_card_{int(card_id):06d}.png" if card_id is not None else "")
            )
            if not image_file or not (cards_dir / image_file).is_file():
                return False
        return True

    def _cleanup_render_cache(self) -> None:
        """Delete temporary draw images older than one day."""
        runtime_dir = self.ctx.paths.runtime_dir
        if not runtime_dir.is_dir():
            return
        cutoff = time.time() - RENDER_CACHE_TTL_SECONDS
        removed = []
        for path in runtime_dir.glob("ongeki_draw_*.png"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed.append(str(path))
            except OSError as exc:
                logger.warning("清理抽卡临时图片失败 %s: %s", path, exc)
        if removed:
            logger.info("已清理 %d 张过期抽卡临时图片", len(removed))

    async def _daily_render_cache_cleanup_loop(self) -> None:
        """Run the daily cleanup loop until the plugin is unloaded."""
        try:
            while True:
                await asyncio.sleep(RENDER_CACHE_TTL_SECONDS)
                self._cleanup_render_cache()
        except asyncio.CancelledError:
            raise

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

    @staticmethod
    def _parse_card_id(kwargs: dict[str, Any]) -> int | None:
        groups = kwargs.get("matched_groups")
        if isinstance(groups, dict):
            raw_id = str(groups.get("card_id") or "").strip()
            if raw_id.isdigit():
                return int(raw_id)
        text = str(kwargs.get("text") or "")
        match = re.search(r"(?<!\d)(\d+)(?!\d)", text)
        return int(match.group(1)) if match is not None else None

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

            weekly_5_claimed = False
            if count == 5:
                weekly_5_claimed = self._db.claim_weekly_5_guarantee(
                    user_id,
                    tz_offset_hours=self.config.economy.tz_offset_hours,
                )
            drawn_cards = self._pool.draw(
                count,
                guarantee=(count == 11 or weekly_5_claimed),
            )
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
                    is_kaika=commitment.is_kaika,
                    is_cho_kaika=commitment.is_cho_kaika,
                )
                for card, commitment in zip(drawn_cards, receipt.commitments, strict=True)
            ]
            summary_lines = [
                f"本次抽取：{count}连",
                f"稀有度：{self._rare_summary(drawn_cards)}",
                f"新卡：{sum(1 for item in receipt.commitments if item.is_new)} 张",
                f"剩余点数：{receipt.points}",
            ]
            if count == 5:
                summary_lines.append(
                    "本周 5 连保底："
                    + (
                        "已使用（本次包含 SR 或以上保底）"
                        if weekly_5_claimed
                        else "本周已使用，本次不再触发 SR 或以上保底"
                    )
                )
            summary = "\n".join(summary_lines)
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
            text = f"签到成功！获得 {receipt.reward} 点"
            if receipt.bonus:
                text += f"，额外获得 {receipt.bonus} 点；！！！超级欧皇，额外获取{receipt.bonus}！！！"
            text += f"｜当前点数：{receipt.points}"
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
            weekly_5_available = self._db.weekly_5_guarantee_available(
                user_id,
                tz_offset_hours=self.config.economy.tz_offset_hours,
            )
        text = (
            f"当前点数：{player.points}｜累计签到 {player.total_checkins} 次"
            f"｜累计抽卡 {player.total_pulls} 次"
            f"｜本周5连保底：{'可用' if weekly_5_available else '已使用'}"
        )
        await self._send_text(stream_id, text)
        return True, text, True

    @Command(
        "ongeki_card_image",
        description="查看已拥有的卡牌高清大图",
        pattern=r"^/(?:卡图|卡面)\s+(?P<card_id>\d+)\s*$",
        aliases=[
            "/og卡图",
            "/og 卡图",
            "/og卡面",
            "/og 卡面",
            "/查看卡图",
            "/查看卡面",
        ],
    )
    async def handle_card_image(
        self,
        stream_id: str = "",
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        """发送指定已拥有卡牌的高清原图。"""
        user_id = self._user_id(kwargs)
        card_id = self._parse_card_id(kwargs)
        if card_id is None:
            text = "用法：/卡图 <卡ID>，例如 /卡图 104490"
            await self._send_text(stream_id, text)
            return True, text, True

        async with self._lock:
            if self._db is None or self._cards is None:
                text = "插件尚未初始化完成，请检查日志"
                await self._send_text(stream_id, text)
                return True, text, True

            inventory = self._db.get_inventory(user_id)
            owned = {entry.card_id for entry in inventory}
            if card_id not in owned:
                text = f"你还没有卡牌 ID {card_id}，无法查看高清大图"
                await self._send_text(stream_id, text)
                return True, text, True

            card = self._cards.by_id.get(card_id)
            if card is None:
                text = f"卡牌 ID {card_id} 不存在"
                await self._send_text(stream_id, text)
                return True, text, True

            card_path = Path(self.config.assets.cards_dir).expanduser() / card.image_file
            if not card_path.is_file():
                text = f"卡牌 ID {card_id} 的图片文件不存在：{card_path}"
                self.ctx.logger.error(text)
                await self._send_text(stream_id, text)
                return True, text, True

            try:
                image_base64 = base64.b64encode(card_path.read_bytes()).decode("ascii")
                await self.ctx.send.image(image_base64, stream_id)
            except Exception as exc:
                self.ctx.logger.error("卡牌图片发送失败: %s", exc, exc_info=True)
                text = f"卡牌图片发送失败：{exc}"
                await self._send_text(stream_id, text)
                return True, text, True

        text = f"已发送卡牌 ID {card_id}：{card.name}"
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

        def rarity_rank(entry: Any) -> tuple[int, int]:
            card = self._cards.by_id.get(entry.card_id)
            rank = RARITY_ORDER.get(card.rarity if card is not None else "", 99)
            return rank, entry.card_id

        entry_by_rarity = sorted(inventory, key=rarity_rank)
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
        lines.append("保底：11连至少 1 张 SR 或以上；5连每用户每周首次至少 1 张 SR 或以上")
        lines.append("5连保底重置：每周四 07:00（按配置时区）")
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
