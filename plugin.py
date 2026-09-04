"""ONGEKI 模拟抽卡插件入口。"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from maibot_sdk import Command, MaiBotPlugin

import asyncio
import base64
import json
import logging
import re
import time
import urllib.error
import urllib.request

from .config_model import OngekiGachaPluginConfig
from .gacha_core import CardCollection, CardInfo, CardPool, RARITY_ORDER, load_cards, rarity_display
from .gacha_db import GachaDatabase
from .gacha_pools import GachaSchedule, PoolCard, PoolEntry
from .gacha_render import GachaRenderer, RenderCard


logger = logging.getLogger(__name__)
RENDER_CACHE_TTL_SECONDS = 24 * 60 * 60
POOL_IMAGE_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_POOL_IMAGE_BYTES = 8 * 1024 * 1024
POOL_KIND_LABELS = {
    "regular": "常驻",
    "limited": "限定",
    "pickup": "UP",
    "attribute": "属性",
    "special": "特殊",
}

class OngekiGachaPlugin(MaiBotPlugin):
    """ONGEKI 模拟抽卡插件。"""

    config_model = OngekiGachaPluginConfig

    def __init__(self) -> None:
        super().__init__()
        self._lock: asyncio.Lock = asyncio.Lock()
        self._cards: CardCollection | None = None
        self._pool: CardPool | None = None
        self._regular_pool: PoolEntry | None = None
        self._regular_pool_instance: CardPool | None = None
        self._schedule: GachaSchedule | None = None
        self._non_gacha_cards: tuple[CardInfo, ...] = ()
        self._db: GachaDatabase | None = None
        self._renderer: GachaRenderer | None = None
        self._cards_dir: Path | None = None
        self._card_info_path: Path | None = None
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
        self._cards_dir = None
        self._card_info_path = None
        self._cards = None
        self._pool = None
        self._regular_pool = None
        self._regular_pool_instance = None
        self._schedule = None
        self._non_gacha_cards = ()
        self._renderer = None

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        """配置更新后热更新卡牌数据、概率和素材目录，不需要重启插件。"""
        del config_data
        del version
        if scope != "self":
            return
        try:
            (
                cards_dir,
                card_info_path,
                cards,
                pool,
                regular_pool,
                regular_pool_instance,
                renderer,
                schedule,
            ) = self._build_runtime(self.config)
        except Exception as exc:
            self.ctx.logger.exception("ONGEKI 模拟抽卡配置热更新失败，保留旧资源: %s", exc)
            return
        async with self._lock:
            self._cards_dir = cards_dir
            self._card_info_path = card_info_path
            self._cards = cards
            self._pool = pool
            self._regular_pool = regular_pool
            self._regular_pool_instance = regular_pool_instance
            self._schedule = schedule
            self._non_gacha_cards = self._non_gacha_cards_from_schedule(cards, schedule)
            self._renderer = renderer
        self.ctx.logger.info("ONGEKI 模拟抽卡配置已热更新")

    def _initialize(self) -> None:
        (
            cards_dir,
            card_info_path,
            cards,
            pool,
            regular_pool,
            regular_pool_instance,
            renderer,
            schedule,
        ) = self._build_runtime(self.config)
        db_path = self.ctx.paths.data_dir / "ongeki_gacha.db"
        database = GachaDatabase(db_path)
        database.open()
        self._cards_dir = cards_dir
        self._card_info_path = card_info_path
        self._cards = cards
        self._pool = pool
        self._regular_pool = regular_pool
        self._regular_pool_instance = regular_pool_instance
        self._schedule = schedule
        self._non_gacha_cards = self._non_gacha_cards_from_schedule(cards, schedule)
        self._db = database
        self._renderer = renderer

    def _build_runtime(
        self,
        config: OngekiGachaPluginConfig,
    ) -> tuple[
        Path,
        Path,
        CardCollection,
        CardPool,
        PoolEntry,
        CardPool,
        GachaRenderer,
        GachaSchedule,
    ]:
        """根据当前配置构建卡牌数据、抽卡池和渲染器。"""
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
        schedule_path = self._resolve_gacha_schedule_path(config)
        schedule = GachaSchedule.load(schedule_path)
        active_pool = self._schedule_pool(schedule, config)
        pool = CardPool(
            cards,
            weight_n=config.pool.weight_n,
            weight_r=config.pool.weight_r,
            weight_sr=config.pool.weight_sr,
            weight_sr_plus=config.pool.weight_sr_plus,
            weight_ssr=config.pool.weight_ssr,
            pool=active_pool,
            pickup_multiplier=config.pool.pickup_multiplier,
            strict_pool_cards=config.pool.strict_pool_cards or active_pool is not None,
        )
        regular_pool = self._build_regular_pool(cards, schedule)
        regular_pool_instance = CardPool(
            cards,
            weight_n=config.pool.weight_n,
            weight_r=config.pool.weight_r,
            weight_sr=config.pool.weight_sr,
            weight_sr_plus=config.pool.weight_sr_plus,
            weight_ssr=config.pool.weight_ssr,
            pool=regular_pool,
            pickup_multiplier=1,
            strict_pool_cards=True,
        )
        renderer = GachaRenderer(cards_dir, Path(__file__).resolve().parent / "assets" / "ui")
        return (
            cards_dir,
            card_info_path,
            cards,
            pool,
            regular_pool,
            regular_pool_instance,
            renderer,
            schedule,
        )

    @staticmethod
    def _today(config: OngekiGachaPluginConfig) -> date:
        raw = GachaDatabase.current_date_str(config.economy.tz_offset_hours)
        return date.fromisoformat(raw)

    @classmethod
    def _schedule_pool(
        cls,
        schedule: GachaSchedule,
        config: OngekiGachaPluginConfig,
    ) -> PoolEntry | None:
        """Select the active pool according to the configured rotation mode."""
        if not schedule.entries:
            return None
        mode = str(config.pool.rotation_mode or "cycle").strip().lower()
        today = cls._today(config)
        if mode == "official":
            return schedule.active_for(today)
        return schedule.cycle_for(today, config.pool.rotation_interval_days)

    @staticmethod
    def _build_regular_pool(
        cards: CardCollection,
        schedule: GachaSchedule,
    ) -> PoolEntry:
        """Return the permanent non-featured card pool."""
        if schedule.regular_pool is not None:
            return schedule.regular_pool
        featured_ids = {
            card_id
            for entry in schedule.entries
            for card_id in entry.cards
        }
        pool_cards = {
            card.id: PoolCard(
                card_id=card.id,
                rarity=card.rarity,
                weight=1,
                is_pickup=False,
                is_select=False,
            )
            for card in cards.cards
            if card.id not in featured_ids
        }
        return PoolEntry(
            pool_id="regular",
            name="常驻池（当前版本已有全部 R/SR/SSR）",
            kind="regular",
            cards=pool_cards,
        )

    @staticmethod
    def _non_gacha_cards_from_schedule(
        cards: CardCollection,
        schedule: GachaSchedule,
    ) -> tuple[CardInfo, ...]:
        """提取排表中标记为非抽卡、可签到掉落的卡牌。"""
        if schedule.non_gacha_pool is None:
            return ()
        return tuple(
            cards.by_id[card_id]
            for card_id in schedule.non_gacha_pool.cards
            if card_id in cards.by_id
        )

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
        configured_is_manual = (
            configured_cards_dir != default_cards_dir
            or configured_card_info_path != default_card_info_path
        )

        if configured_ready and configured_is_manual:
            logger.info(
                "使用手动配置的卡牌数据路径（卡面: %s，卡牌信息: %s）",
                configured_cards_dir,
                configured_card_info_path,
            )
            return configured_cards_dir, configured_card_info_path

        default_ready = cls._card_data_quick_ready(
            default_cards_dir,
            default_card_info_path,
        )
        if default_ready:
            if configured_ready and not configured_is_manual:
                logger.info(
                    "使用默认卡牌数据（卡面: %s，卡牌信息: %s）",
                    default_cards_dir,
                    default_card_info_path,
                )
            elif configured_is_manual:
                logger.warning(
                    "手动配置的卡牌数据不可用，回退到默认数据（卡面: %s，卡牌信息: %s）",
                    default_cards_dir,
                    default_card_info_path,
                )
            return default_cards_dir, default_card_info_path

        if configured_ready:
            logger.info(
                "使用可用的手动配置卡牌数据（卡面: %s，卡牌信息: %s）",
                configured_cards_dir,
                configured_card_info_path,
            )
            return configured_cards_dir, configured_card_info_path

        raise FileNotFoundError(
            "卡牌数据不可用。请先运行 sync_card_data.py，"
            "或在配置 assets.cards_dir / assets.card_info_json 中填写有效绝对路径。"
        )

    def _resolve_gacha_schedule_path(self, config: OngekiGachaPluginConfig) -> Path:
        """Choose a gacha schedule file, falling back to the bundled default."""
        plugin_root = Path(__file__).resolve().parent
        default_path = plugin_root / "assets" / "card_data" / "gacha_pools.json"
        configured = Path(config.pool.schedule_json or "").expanduser()
        if not configured.is_absolute():
            configured = plugin_root / configured
        configured = configured.resolve()
        if configured.is_file():
            return configured
        if default_path.is_file() and configured != default_path.resolve():
            logger.warning("卡池排表不存在，回退到默认文件: %s", default_path)
            return default_path
        return configured

    @staticmethod
    def _card_data_quick_ready(cards_dir: Path, card_info_path: Path) -> bool:
        """Quick check: configured JSON and its referenced PNGs all exist."""
        if not cards_dir.is_dir() or not card_info_path.is_file():
            return False
        try:
            rows = json.loads(card_info_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return False
        if not isinstance(rows, list):
            return False
        checked = False
        for row in rows:
            if not isinstance(row, dict):
                return False
            if not bool(row.get("imagePresent", False)):
                continue
            checked = True
            card_id = row.get("id")
            image_file = str(
                row.get("imageFile")
                or (f"ui_card_{int(card_id):06d}.png" if card_id is not None else "")
            )
            if not image_file or not (cards_dir / image_file).is_file():
                return False
        return checked

    def _help_text(self) -> str:
        """按当前配置生成 /帮助 返回文案。"""
        return (
            "音击抽卡模拟器（MaiBot 本地娱乐插件）\n"
            "/签到　/抽卡　/点数\n"
            "/月卡　/卡册　/卡图\n"
            "/卡池　/天井　/概率\n"
            "/天井列表　/规则　/帮助\n"
            "详细用法发送 /规则"
        )

    def _rules_text(self) -> list[str]:
        """生成完整规则说明，供转发消息使用。"""
        economy = self.config.economy
        monthly = self.config.monthly_card
        return [
            "音击抽卡模拟器 · 详细规则",
            "【签到与奖励】",
            f"每日基础签到：{economy.min_reward}～{economy.max_reward} 点",
            (
                f"连续签到第 7 天额外 +{economy.streak_weekly_reward} 点；"
                f"连续第 {economy.streak_cycle_days} 天额外 +"
                f"{economy.streak_cycle_reward} 点"
            ),
            (
                f"囤点档位：{economy.savings_threshold_1}/"
                f"{economy.savings_threshold_2}/{economy.savings_threshold_3} "
                f"点，对应奖励 "
                f"{economy.savings_bonus_1}/{economy.savings_bonus_2}/"
                f"{economy.savings_bonus_3} 点"
            ),
            "【抽卡】",
            f"消耗：1 连 {economy.cost_1} 点、5 连 {economy.cost_5} 点、11 连 {economy.cost_11} 点",
            "抽卡流程：先按稀有度权重，再在池内按卡权重抽取，UP 卡按配置倍率加权",
            "11 连必得 SR 或以上；5 连每用户每周首次触发一次 SR 或以上保底",
            "【卡池】",
            f"默认每 {self.config.pool.rotation_interval_days} 天轮换一个历史官方卡池；也可切换为按官方日期选池",
            "活动池候选为当期版本已有全部 R/SR/SSR；官方公告未写 UP 时会显示 UP 卡：0 张，但仍抽取这些基础卡",
            "常驻池包含当前版本已有的全部 R/SR/SSR 基础卡，可用 /抽卡 常驻 单独抽取",
            "【天井】",
            "抽卡每张 +1 点天井点；达到上限后可 /天井 <卡ID> 兑换当前池选择卡",
            "/天井列表（或 /天井 列表）可查看当前池全部可选卡的 ID 与角色",
            "每个卡池只可兑换一次，兑换后清空该池天井点",
            "【月卡】",
            (
                f"价格：{monthly.price} 点，有效期 {monthly.duration_days} 天，"
                f"每日签到额外 +{monthly.daily_bonus} 点"
            ),
            (
                f"购买/续费获得 {monthly.half_price_5_pull_count} 次半价五连；"
                f"剩余不超过 {monthly.renew_max_remaining_days} 天可续费"
            ),
            "【管理员】",
            "管理员可通过 /奖励 @用户 <点数> [备注] 发放点数，发送记录会写入审计日志",
            "【说明】",
            "所有点数、概率和奖励均为本地娱乐设定，不代表 SEGA 官方规则",
        ]

    @staticmethod
    def _monthly_card_remaining_days(expires_at: str, tz_offset_hours: int) -> int:
        """按配置时区计算月卡剩余天数。"""
        today = GachaDatabase.current_date_str(tz_offset_hours)
        return GachaDatabase.monthly_card_remaining_days(expires_at, today)

    @staticmethod
    def _timezone_label(offset_hours: int) -> str:
        """把 UTC 偏移小时数显示为 UTC+8 等格式。"""
        sign = "+" if offset_hours >= 0 else ""
        return f"UTC{sign}{offset_hours}"

    @staticmethod
    def _monthly_card_action(kwargs: dict[str, Any]) -> str:
        """解析月卡命令中的购买/续费动作。"""
        groups = kwargs.get("matched_groups")
        if isinstance(groups, dict):
            raw = str(groups.get("action") or "").strip()
            if raw in {"购买", "买", "续费", "续"}:
                return raw
            if raw == "查看":
                return ""
        text = str(kwargs.get("text") or "")
        match = re.search(r"购买|买|续费|续", text)
        return match.group(0) if match is not None else ""

    def _is_admin(self, user_id: str) -> bool:
        """判断当前用户是否在管理员白名单中。"""
        if user_id == "local-operator" and self.config.admin.allow_local_operator:
            return True
        raw_ids = self.config.admin.admin_ids or []
        return any(
            str(item).strip() == user_id
            for item in raw_ids
            if str(item).strip()
        )

    @staticmethod
    def _parse_grant(kwargs: dict[str, Any]) -> tuple[str, int, str] | None:
        """解析奖励指令的目标 QQ、点数和可选备注。"""
        groups = kwargs.get("matched_groups")
        if isinstance(groups, dict):
            target_id = str(groups.get("target_id") or "").strip()
            target_at = str(groups.get("target_at") or "").strip()
            raw_amount = str(groups.get("amount") or "").strip()
            note = str(groups.get("note") or "").strip()
            if raw_amount.isdigit() and int(raw_amount) > 0:
                if target_id.isdigit():
                    return target_id, int(raw_amount), note
                if target_at.startswith("@"):
                    resolved_id = OngekiGachaPlugin._extract_at_target_id(kwargs)
                    if resolved_id:
                        return resolved_id, int(raw_amount), note
                    literal_id = target_at[1:].strip()
                    if literal_id.isdigit():
                        return literal_id, int(raw_amount), note

        text = str(kwargs.get("text") or "")
        match = re.search(
            r"@([^\s]+)\s+(\d+)(?:\s+(.+))?",
            text,
        )
        if match is None:
            match = re.search(
                r"(?<!\d)(\d+)\s+(\d+)(?:\s+(.+))?",
                text,
            )
        if match is None:
            return None
        amount = int(match.group(2))
        if amount <= 0:
            return None
        if match.group(0).startswith("@"):
            resolved_id = OngekiGachaPlugin._extract_at_target_id(kwargs)
            if resolved_id:
                return resolved_id, amount, str(match.group(3) or "").strip()
            literal_id = match.group(1).strip()
            if literal_id.isdigit():
                return literal_id, amount, str(match.group(3) or "").strip()
        return match.group(1), amount, str(match.group(3) or "").strip()

    @staticmethod
    def _extract_at_target_id(kwargs: dict[str, Any]) -> str | None:
        """从 MaiBot 传入的原始消息组件中提取被 @ 目标的 QQ。"""
        message = kwargs.get("message")
        if not isinstance(message, dict):
            return None
        raw_message = message.get("raw_message")
        if not isinstance(raw_message, list):
            return None
        for component in raw_message:
            if not isinstance(component, dict):
                continue
            if str(component.get("type") or "").strip() != "at":
                continue
            data = component.get("data")
            if not isinstance(data, dict):
                continue
            target_id = str(data.get("target_user_id") or "").strip()
            if target_id.isdigit():
                return target_id
        return None

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
    def _is_regular_draw(kwargs: dict[str, Any]) -> bool:
        """Detect an explicit request for the permanent regular pool."""
        groups = kwargs.get("matched_groups")
        if isinstance(groups, dict):
            raw = str(groups.get("pool") or "").strip()
            if raw in {"常驻", "普通", "常规"}:
                return True
        text = str(kwargs.get("text") or "")
        return any(keyword in text for keyword in ("常驻", "普通", "常规"))

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

    def _sync_active_pool(self) -> None:
        """Refresh the active pool when the calendar/rotation changes."""
        if self._pool is None or self._schedule is None:
            return
        active = self._schedule_pool(self._schedule, self.config)
        if active is not None and active.pool_id == self._pool.pool_id:
            return
        self._pool.set_pool(active)

    def _cycle_index(self, schedule: GachaSchedule) -> int | None:
        if not schedule.entries:
            return None
        interval = max(int(self.config.pool.rotation_interval_days), 1)
        today = self._today(self.config)
        return (today.toordinal() // interval) % len(schedule.entries)

    def _savings_thresholds(self) -> tuple[int, ...]:
        config = self.config.economy
        return (
            config.savings_threshold_1,
            config.savings_threshold_2,
            config.savings_threshold_3,
        )

    def _savings_bonuses(self) -> tuple[int, ...]:
        config = self.config.economy
        return (
            config.savings_bonus_1,
            config.savings_bonus_2,
            config.savings_bonus_3,
        )

    def _current_pool_info(self) -> tuple[PoolEntry | None, str, int | None]:
        """Return (current pool, mode, cycle index) for commands."""
        self._sync_active_pool()
        if self._schedule is None:
            return None, "cycle", None
        mode = str(self.config.pool.rotation_mode or "cycle").strip().lower()
        today = self._today(self.config)
        if mode == "official":
            return self._schedule.active_for(today), mode, None
        index = self._cycle_index(self._schedule)
        pool = self._schedule.entries[index] if index is not None else None
        return pool, mode, index

    @staticmethod
    def _download_pool_image(url: str) -> bytes:
        """Download and validate an official announcement image."""
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            content_type = str(response.headers.get("Content-Type", "")).lower()
            if content_type and not content_type.startswith("image/"):
                raise RuntimeError(f"官方图片 Content-Type 不是图片: {content_type}")
            data = response.read(MAX_POOL_IMAGE_BYTES + 1)
        if len(data) > MAX_POOL_IMAGE_BYTES:
            raise RuntimeError("官方图片超过 8 MiB 上限")
        if not data:
            raise RuntimeError("官方图片内容为空")
        return data

    async def _send_official_pool_image(self, stream_id: str, image_url: str) -> bool:
        """Fetch/cache an official pool image and send it to the chat."""
        if not image_url:
            return False
        runtime_dir = self.ctx.paths.runtime_dir
        cache_name = re.sub(r"[^A-Za-z0-9_-]+", "_", image_url.split("/")[-1] or "pool")[:80]
        cache_path = runtime_dir / f"ongeki_pool_{cache_name}"
        try:
            if cache_path.is_file():
                age = time.time() - cache_path.stat().st_mtime
                if age <= POOL_IMAGE_CACHE_TTL_SECONDS:
                    image_bytes = cache_path.read_bytes()
                else:
                    image_bytes = await asyncio.to_thread(self._download_pool_image, image_url)
                    cache_path.write_bytes(image_bytes)
            else:
                runtime_dir.mkdir(parents=True, exist_ok=True)
                image_bytes = await asyncio.to_thread(self._download_pool_image, image_url)
                cache_path.write_bytes(image_bytes)
            image_base64 = base64.b64encode(image_bytes).decode("ascii")
            await self.ctx.send.image(image_base64, stream_id)
            return True
        except Exception as exc:
            self.ctx.logger.warning("发送卡池官方图片失败 %s: %s", image_url, exc)
            return False

    async def _send_text(self, stream_id: str, text: str) -> None:
        await self.ctx.send.text(text, stream_id)

    async def _send_forward(self, stream_id: str, lines: list[str]) -> None:
        """Send a long message as a merged forward message."""
        nodes = [
            {
                "user_id": "ongeki-gacha",
                "nickname": "音击抽卡模拟器",
                "message_id": f"ongeki_{index:03d}",
                "segments": [{"type": "text", "data": line}],
            }
            for index, line in enumerate(lines)
            if line
        ]
        if not nodes:
            return
        try:
            await self.ctx.send.forward(nodes, stream_id)
        except Exception as exc:
            self.ctx.logger.warning("转发消息发送失败，回退为普通文本: %s", exc)
            await self._send_text(stream_id, "\n".join(lines))

    async def _send_lines(
        self,
        stream_id: str,
        lines: list[str] | str,
        *,
        force_forward: bool = False,
    ) -> None:
        """Send short text normally and long content as a forward message."""
        if isinstance(lines, str):
            lines = lines.splitlines()
        text = "\n".join(lines).strip()
        if not text:
            return
        if force_forward or len(lines) > 8 or len(text) > 700:
            await self._send_forward(stream_id, lines)
        else:
            await self._send_text(stream_id, text)

    @staticmethod
    def _rare_summary(cards: list[CardInfo]) -> str:
        counts = Counter(rarity_display(card.rarity) for card in cards)
        parts = []
        for label in ("SSR", "SR+", "SR", "R", "N"):
            if counts[label]:
                parts.append(f"{label}×{counts[label]}")
        return " · ".join(parts) if parts else "无"

    @staticmethod
    def _card_display_name(card: CardInfo, limit: int | None = None) -> str:
        """返回去掉开头【稀有度】标签后的卡名，避免列表里重复显示稀有度。"""
        name = re.sub(r"^【[^】]+】\s*", "", card.name).strip() or card.name
        return name if limit is None else name[:limit]

    @staticmethod
    def _pool_kind_label(kind: str) -> str:
        """把排表里的内部类型转换为用户可读的中文标签。"""
        normalized = str(kind or "").strip().lower()
        return POOL_KIND_LABELS.get(normalized, str(kind or ""))

    # ==================== 命令 ====================

    @Command(
        "ongeki_draw",
        description="音击抽卡模拟器，支持 1/5/11 连",
        pattern=r"^/抽卡(?:\s+(?P<pool>常驻|普通|常规))?(?:\s+(?P<count>11|5|1))?\s*$",
        aliases=["/og抽卡", "/og 抽卡", "/gacha"],
    )
    async def handle_draw(self, stream_id: str = "", **kwargs: dict[str, Any]) -> tuple[bool, str, bool]:
        """执行抽卡并发送结果图。"""
        user_id = self._user_id(kwargs)
        count = self._parse_count(kwargs)
        regular_draw = self._is_regular_draw(kwargs)
        cost = self._cost(count)
        if cost <= 0:
            text = "抽卡数量只能为 1、5 或 11"
            await self._send_text(stream_id, text)
            return True, text, True

        async with self._lock:
            if (
                self._db is None
                or self._renderer is None
                or self._cards is None
                or (regular_draw and self._regular_pool_instance is None)
                or (not regular_draw and self._pool is None)
            ):
                text = "插件尚未初始化完成，请检查日志"
                await self._send_text(stream_id, text)
                return True, text, True
            self._sync_active_pool()
            draw_pool = self._regular_pool_instance if regular_draw else self._pool
            if draw_pool is None:
                text = "插件尚未初始化完成，请检查日志"
                await self._send_text(stream_id, text)
                return True, text, True

            player = self._db.get_player(user_id)
            original_cost = cost
            half_price_used = False
            if count == 5 and self._db.half_price_5_pull_available(user_id):
                half_price_cost = cost // 2
                if player.points < half_price_cost:
                    text = (
                        f"点数不足（月卡半价需要 {half_price_cost} 点，"
                        f"当前 {player.points} 点）。请先 /签到"
                    )
                    await self._send_text(stream_id, text)
                    return True, text, True
                if self._db.claim_half_price_5_pull(user_id):
                    half_price_used = True
                    cost = half_price_cost
            if player.points < cost:
                text = f"点数不足（需要 {cost} 点，当前 {player.points} 点）。请先 /签到"
                await self._send_text(stream_id, text)
                return True, text, True

            weekly_5_claimed = False
            if count == 5:
                weekly_5_claimed = self._db.claim_weekly_5_guarantee(
                    user_id,
                    tz_offset_hours=self.config.economy.tz_offset_hours,
                )
            drawn_cards = draw_pool.draw(
                count,
                guarantee=(count == 11 or weekly_5_claimed),
            )
            receipt = self._db.commit_draw(
                user_id,
                [(card.id, card.rarity) for card in drawn_cards],
                cost=cost,
                pool_id=draw_pool.pool_id,
                max_select_points=draw_pool.pool_select_points or 0,
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
                f"本次抽取：{count} 连｜卡池：{draw_pool.pool_name[:60]}",
                f"稀有度：{self._rare_summary(drawn_cards)}",
                f"新卡：{sum(1 for item in receipt.commitments if item.is_new)} 张",
                f"剩余点数：{receipt.points} 点",
            ]
            if draw_pool.featured_count:
                summary_lines.append(
                    f"UP：{draw_pool.featured_count} 张（权重 ×{draw_pool.pickup_multiplier}）"
                )
            if receipt.max_select_points:
                if receipt.select_claimed:
                    ceiling_text = f"天井已兑换（{receipt.max_select_points} 点）"
                elif receipt.select_ready:
                    ceiling_text = (
                        f"天井已满（{receipt.select_points}/"
                        f"{receipt.max_select_points}），可 /天井 <卡ID> 兑换"
                    )
                else:
                    ceiling_text = (
                        f"天井进度：{receipt.select_points}/"
                        f"{receipt.max_select_points}"
                    )
                summary_lines.append(ceiling_text)
            if count == 5:
                summary_lines.append(
                    "本周 5 连保底："
                    + (
                        "已使用（本次已触发 SR 或以上保底）"
                        if weekly_5_claimed
                        else "本周已使用，本次不再触发 SR 或以上保底"
                    )
                )
                if half_price_used:
                    summary_lines.append(
                        f"月卡半价已使用：原价 {original_cost} 点，实际 {cost} 点"
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

    @Command("ongeki_checkin", description="每日签到领取基础、连续签到、卡池周期和月卡奖励", pattern=r"^/签到\s*$", aliases=["/og签到", "/og 签到", "/打卡"])
    async def handle_checkin(self, stream_id: str = "", **kwargs: dict[str, Any]) -> tuple[bool, str, bool]:
        """执行每日签到。"""
        user_id = self._user_id(kwargs)
        config = self.config.economy
        savings_bonus = 0
        final_points = 0
        async with self._lock:
            if self._db is None:
                text = "插件尚未初始化完成，请检查日志"
                await self._send_text(stream_id, text)
                return True, text, True
            receipt = self._db.daily_checkin(
                user_id,
                min_reward=config.min_reward,
                max_reward=config.max_reward,
                streak_daily_step=config.streak_daily_step,
                streak_daily_max=config.streak_daily_max,
                streak_weekly_reward=config.streak_weekly_reward,
                streak_cycle_days=config.streak_cycle_days,
                streak_cycle_reward=config.streak_cycle_reward,
                monthly_daily_bonus=self.config.monthly_card.daily_bonus,
                non_gacha_card_ids=[
                    (card.id, card.rarity)
                    for card in self._non_gacha_cards
                ],
                non_gacha_checkin_probability=config.non_gacha_checkin_probability,
                tz_offset_hours=config.tz_offset_hours,
            )
            if receipt.success:
                savings_bonus, final_points = self._db.grant_savings_bonus(
                    user_id,
                    thresholds=self._savings_thresholds(),
                    bonuses=self._savings_bonuses(),
                )
        if receipt.success:
            parts = [
                f"签到成功！基础获得 {receipt.reward} 点",
                f"连续签到第 {receipt.streak_days} 天",
            ]
            if receipt.streak_extra:
                parts.append(f"连续签到额外 +{receipt.streak_extra} 点")
            if receipt.weekly_reward:
                parts.append(f"连续 7 天奖励 +{receipt.weekly_reward} 点")
            if receipt.cycle_reward:
                parts.append(f"卡池周期奖励 +{receipt.cycle_reward} 点")
            if receipt.monthly_reward:
                parts.append(f"月卡每日奖励 +{receipt.monthly_reward} 点")
            if savings_bonus:
                parts.append(f"囤点奖励 +{savings_bonus} 点")
            if receipt.non_gacha_card_id:
                bonus_card = (
                    self._cards.by_id.get(receipt.non_gacha_card_id)
                    if self._cards is not None
                    else None
                )
                if bonus_card is not None:
                    parts.append(f"签到彩蛋卡：{self._card_display_name(bonus_card)}")
            text = "，".join(parts)
            text += f"｜当前点数：{final_points or receipt.points} 点"
        else:
            text = receipt.error or "签到失败"
        await self._send_text(stream_id, text)
        return True, text, True

    @Command("ongeki_points", description="查看点数、连续签到和月卡状态", pattern=r"^/(?:点数|余额)\s*$", aliases=["/og点数", "/og 点数", "/og余额"])
    async def handle_points(self, stream_id: str = "", **kwargs: dict[str, Any]) -> tuple[bool, str, bool]:
        """查看玩家点数。"""
        user_id = self._user_id(kwargs)
        async with self._lock:
            if self._db is None:
                text = "插件尚未初始化完成，请检查日志"
                await self._send_text(stream_id, text)
                return True, text, True
            savings_bonus, _ = self._db.grant_savings_bonus(
                user_id,
                thresholds=self._savings_thresholds(),
                bonuses=self._savings_bonuses(),
            )
            player = self._db.get_player(user_id)
            weekly_5_available = self._db.weekly_5_guarantee_available(
                user_id,
                tz_offset_hours=self.config.economy.tz_offset_hours,
            )
            remaining_days = self._monthly_card_remaining_days(
                player.monthly_card_expires_at,
                self.config.economy.tz_offset_hours,
            )
            if remaining_days > 0:
                monthly_text = f"月卡剩余 {remaining_days} 天"
            elif player.monthly_card_purchase_count > 0:
                monthly_text = "月卡已过期"
            else:
                monthly_text = "月卡未购买"
        text = (
            f"当前点数：{player.points} 点｜累计签到：{player.total_checkins} 次"
            f"｜累计抽卡：{player.total_pulls} 次"
            f"｜连续签到：{player.streak_days} 天"
            f"｜{monthly_text}"
            f"｜月卡半价五连：{player.half_price_5_pull_count} 次"
            f"｜囤点档位：{player.savings_bonus_level}/3"
            f"｜本周 5 连保底：{'可用' if weekly_5_available else '已使用'}"
        )
        if savings_bonus:
            text += f"｜囤点奖励 +{savings_bonus} 点"
        await self._send_text(stream_id, text)
        return True, text, True

    @Command(
        "ongeki_monthly_card",
        description="购买、续费或查看月卡",
        pattern=r"^/(?:月卡(?:购买)?|购买月卡)(?:\s+(?P<action>购买|买|续费|续|查看))?\s*$",
        aliases=["/og月卡", "/og 月卡", "/月卡购买"],
    )
    async def handle_monthly_card(
        self,
        stream_id: str = "",
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        """查看月卡状态，或购买/续费月卡。"""
        user_id = self._user_id(kwargs)
        action = self._monthly_card_action(kwargs)
        config = self.config.monthly_card
        async with self._lock:
            if self._db is None:
                text = "插件尚未初始化完成，请检查日志"
                await self._send_text(stream_id, text)
                return True, text, True
            player = self._db.get_player(user_id)
            remaining_days = self._monthly_card_remaining_days(
                player.monthly_card_expires_at,
                self.config.economy.tz_offset_hours,
            )
            if action:
                receipt = self._db.purchase_monthly_card(
                    user_id,
                    price=config.price,
                    duration_days=config.duration_days,
                    renew_max_remaining_days=config.renew_max_remaining_days,
                    half_price_5_pull_count=config.half_price_5_pull_count,
                    tz_offset_hours=self.config.economy.tz_offset_hours,
                )
                if receipt.success:
                    verb = "续费" if remaining_days > 0 else "购买"
                    text = (
                        f"月卡{verb}成功！消耗 {config.price} 点\n"
                        f"有效期至：{receipt.expires_at}（剩余 {receipt.remaining_days} 天）\n"
                        f"每日签到额外：+{config.daily_bonus} 点\n"
                        f"月卡半价五连：+{config.half_price_5_pull_count} 次\n"
                        f"当前点数：{receipt.points} 点"
                    )
                else:
                    text = receipt.error or "月卡购买失败"
            else:
                if remaining_days > 0:
                    status = f"有效，剩余 {remaining_days} 天"
                elif player.monthly_card_purchase_count > 0:
                    status = "已过期"
                else:
                    status = "未购买"
                text = "\n".join(
                    [
                        f"月卡状态：{status}",
                        f"价格：{config.price} 点 / {config.duration_days} 天",
                        f"每日签到额外：+{config.daily_bonus} 点",
                        f"续费条件：剩余不超过 {config.renew_max_remaining_days} 天",
                        f"半价五连剩余：{player.half_price_5_pull_count} 次",
                        f"当前点数：{player.points} 点",
                        "操作：/月卡 购买；/月卡 续费",
                    ]
                )
        await self._send_text(stream_id, text)
        return True, text, True

    @Command(
        "ongeki_grant_points",
        description="管理员向指定 QQ 用户发放点数",
        pattern=r"^/(?:奖励|发放点数|发点数)\s+(?:(?P<target_at>@\S+)|(?P<target_id>\d+))\s+(?P<amount>\d+)(?:\s+(?P<note>.+))?\s*$",
        aliases=["/奖励", "/发点数", "/发放点数"],
    )
    async def handle_grant_points(
        self,
        stream_id: str = "",
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        """管理员向指定 QQ 用户发放点数。"""
        user_id = self._user_id(kwargs)
        parsed = self._parse_grant(kwargs)
        if parsed is None:
            text = "用法：/奖励 @用户 <点数> [备注]，或 /奖励 <QQ号> <点数> [备注]"
            await self._send_text(stream_id, text)
            return True, text, True
        target_id, amount, note = parsed
        if not self._is_admin(user_id):
            text = "你不是管理员，无法发放点数"
            await self._send_text(stream_id, text)
            return True, text, True

        async with self._lock:
            if self._db is None:
                text = "插件尚未初始化完成，请检查日志"
                await self._send_text(stream_id, text)
                return True, text, True
            receipt = self._db.grant_points(
                user_id,
                target_id,
                amount,
                note=note,
            )
        if receipt.success:
            text = (
                f"发放成功：已向 {target_id} 发放 {amount} 点，"
                f"当前点数：{receipt.points} 点"
            )
            if note:
                text += f"\n备注：{note}"
        else:
            text = receipt.error or "发放点数失败"
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
            text = "用法：/卡图 <卡牌ID>，例如 /卡图 104490"
            await self._send_text(stream_id, text)
            return True, text, True

        async with self._lock:
            if self._db is None or self._cards is None or self._cards_dir is None:
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

            card_path = self._card_image_path(card)
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

        text = f"已发送卡牌 ID {card_id}：{self._card_display_name(card)}"
        await self._send_text(stream_id, text)
        return True, text, True

    def _card_image_path(self, card: CardInfo) -> Path:
        """返回当前运行实例实际使用的卡面文件路径。"""
        if self._cards_dir is None:
            raise RuntimeError("卡牌数据目录尚未初始化")
        return self._cards_dir / card.image_file

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
            short_name = self._card_display_name(card, 36)
            preview_lines.append(f"{rarity_display(card.rarity)} {short_name} ×{entry.copies}")
        if preview_lines:
            lines.append("部分收藏：")
            lines.extend(preview_lines)
        text = "\n".join(lines)
        await self._send_text(stream_id, text)
        return True, text, True

    @Command(
        "ongeki_pool",
        description="查看本期卡池、UP 卡与轮替信息",
        pattern=r"^/(?:卡池|卡池轮替)(?:\s+(?P<action>列表|下一期))?\s*$",
        aliases=["/og卡池", "/og 卡池", "/池子", "/卡池详情"],
    )
    async def handle_pool(
        self,
        stream_id: str = "",
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        """显示当前卡池和历史轮替信息。"""
        user_id = self._user_id(kwargs)
        groups = kwargs.get("matched_groups")
        action = (
            str(groups.get("action") or "").strip()
            if isinstance(groups, dict)
            else ""
        )
        del kwargs
        image_url = ""
        async with self._lock:
            if self._pool is None or self._schedule is None:
                text = "插件尚未初始化完成，请检查日志"
                await self._send_text(stream_id, text)
                return True, text, True
            self._sync_active_pool()
            schedule = self._schedule
            mode = str(self.config.pool.rotation_mode or "cycle").strip().lower()
            today = self._today(self.config)
            index = self._cycle_index(schedule)

            if mode == "official":
                active = schedule.active_for(today)
                if active is None:
                    lines = [
                        "本期卡池：常驻池（当前版本已有全部 R/SR/SSR）",
                        "状态：当前没有官方活动池，使用常驻池",
                    ]
                else:
                    lines = [
                        f"本期卡池：{active.name[:60]}",
                        f"类型：{self._pool_kind_label(active.kind)}",
                        f"时间：{active.start_date} ～ {active.end_date}",
                    ]
            else:
                if index is None:
                    lines = ["卡池排表为空"]
                else:
                    active = schedule.entries[index]
                    lines = [
                        f"本期卡池：{active.name[:60]}",
                        f"类型：{self._pool_kind_label(active.kind)}",
                        (
                            f"时间：{active.start_date} ～ {active.end_date}"
                            if active.start_date and active.end_date
                            else "时间：历史轮替周期"
                        ),
                        f"历史轮替：第 {index + 1}/{len(schedule.entries)} 期",
                    ]
                    rotation_day = today + timedelta(
                        days=self.config.pool.rotation_interval_days
                    )
                    lines.append(
                        f"下次轮替：约 {rotation_day.isoformat()}（每 "
                        f"{self.config.pool.rotation_interval_days} 天）"
                    )

            shown_pool = (
                active
                if mode == "official"
                else (schedule.entries[index] if index is not None else None)
            )
            image_url = shown_pool.image_url if shown_pool is not None else ""
            if shown_pool is not None and self._cards is not None:
                up_ssr = shown_pool.up_ssr_cards()
                if up_ssr:
                    lines.append(f"UP SSR：{len(up_ssr)} 张")
                    if action == "列表":
                        preview = up_ssr
                    else:
                        preview = up_ssr[:6]
                    for pool_card in preview:
                        card = self._cards.by_id.get(pool_card.card_id)
                        if card is None:
                            continue
                        lines.append(
                            f"{self._card_display_name(card, 38)}（ID {card.id}）"
                        )
                    if action != "列表" and len(up_ssr) > len(preview):
                        lines.append("完整列表：/卡池 列表")
            if shown_pool is not None and self._db is not None:
                state = self._db.get_pool_select_state(
                    user_id,
                    shown_pool.pool_id,
                )
                if state.max_select_points > 0:
                    if state.is_claimed:
                        ceiling_status = "本池已兑换"
                    elif state.is_ready:
                        ceiling_status = (
                            f"已满 {state.select_points}/"
                            f"{state.max_select_points}，可 /天井 <卡ID>"
                        )
                    else:
                        ceiling_status = (
                            f"{state.select_points}/"
                            f"{state.max_select_points}"
                        )
                    lines.append(f"你的天井：{ceiling_status}")
            if self._regular_pool is not None:
                regular_pool_name = (
                    self._regular_pool_instance.pool_name[:60]
                    if self._regular_pool_instance is not None
                    else self._regular_pool.name[:60]
                )
                lines.append(
                    f"常驻池：{regular_pool_name}"
                    f"（{len(self._regular_pool.cards)} 张，可 /抽卡 常驻 使用）"
                )
            if self._pool is not None:
                pool_up_text = f"UP 卡：{self._pool.featured_count} 张"
                if self._pool.select_count:
                    pool_up_text += f"｜天井选择：{self._pool.select_count} 张"
                if self._pool.featured_count:
                    lines.append(
                        pool_up_text
                        + f"（权重 ×{self._pool.pickup_multiplier}）"
                    )
                else:
                    lines.append(
                        pool_up_text
                        + "（本期无 UP，候选为当期版本已有全部 R/SR/SSR）"
                    )
                if self._pool.select_count:
                    lines.append("天井选择 ID 与角色：/天井列表（或 /天井 列表）")
                if self._pool.pool_select_points:
                    lines.append(f"天井：{self._pool.pool_select_points} 点")
            if action in {"列表", "下一期"} and index is not None:
                future = []
                for offset in range(1, 4 if action == "列表" else 2):
                    next_pool = schedule.entries[(index + offset) % len(schedule.entries)]
                    future.append(f"{next_pool.name[:30]}")
                if future:
                    lines.append("未来轮替：" + "；".join(future))
            lines.append("排表来源：SEGA CARDMAKER 官方公告（2020-10～2026-07）+ Artemis 权重")
            text = "\n".join(lines)
        await self._send_lines(stream_id, text)
        if image_url:
            await self._send_official_pool_image(stream_id, image_url)
        return True, text, True

    @Command(
        "ongeki_ceiling",
        description="查看或兑换当前卡池天井选择卡",
        pattern=(
            r"^/(?:天井|天井兑换|卡池兑换|兑换天井)"
            r"(?:\s+(?P<action>列表|查看))?"
            r"(?:\s+(?P<card_id>\d+))?\s*$"
        ),
        aliases=["/og天井", "/og 天井", "/兑换天井"],
    )
    async def handle_ceiling(
        self,
        stream_id: str = "",
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        """查看天井状态，或在天井满后兑换选择卡。"""
        user_id = self._user_id(kwargs)
        groups = kwargs.get("matched_groups")
        action = (
            str(groups.get("action") or "").strip()
            if isinstance(groups, dict)
            else ""
        )
        raw_card_id = (
            str(groups.get("card_id") or "").strip()
            if isinstance(groups, dict)
            else ""
        )
        card_id = int(raw_card_id) if raw_card_id.isdigit() else None
        del kwargs

        async with self._lock:
            if self._db is None or self._cards is None:
                text = "插件尚未初始化完成，请检查日志"
                await self._send_text(stream_id, text)
                return True, text, True
            pool, _, _ = self._current_pool_info()
            if pool is None:
                text = "当前没有活动池，无法使用天井"
                await self._send_text(stream_id, text)
                return True, text, True

            max_points = pool.select_points or 0
            state = self._db.get_pool_select_state(user_id, pool.pool_id)
            selectable = [
                pool_card
                for pool_card in pool.cards.values()
                if pool_card.is_select
            ]
            selectable_ids = {item.card_id for item in selectable}

            if action == "列表":
                lines = [
                    f"当前卡池：{pool.name[:60]}",
                    f"天井上限：{max_points} 点",
                    f"天井进度：{state.select_points}/{max_points}"
                    if max_points
                    else "当前卡池无天井",
                    f"可选卡数量：{len(selectable)}",
                    "发送 /天井 <卡ID> 可兑换指定卡",
                ]
                for pool_card in selectable:
                    card = self._cards.by_id.get(pool_card.card_id)
                    if card is None:
                        continue
                    lines.append(
                        f"{rarity_display(card.rarity)} "
                        f"{self._card_display_name(card, 38)}（ID {card.id}）"
                    )
                text = "\n".join(lines)
            elif card_id is None:
                if max_points <= 0:
                    text = "当前卡池没有天井机制"
                elif state.is_claimed:
                    text = (
                        f"当前卡池天井已兑换（{max_points} 点）"
                    )
                elif state.is_ready:
                    text = (
                        f"天井已满：{state.select_points}/{max_points} 点\n"
                        f"可选卡 {len(selectable)} 张\n"
                        "发送 /天井 <卡ID> 兑换指定卡"
                    )
                else:
                    text = (
                        f"天井进度：{state.select_points}/{max_points} 点\n"
                        f"可选卡 {len(selectable)} 张"
                    )
            else:
                if max_points <= 0:
                    text = "当前卡池没有天井机制"
                elif state.is_claimed:
                    text = "当前卡池已经兑换过天井卡"
                elif not state.is_ready:
                    text = (
                        f"天井尚未满：{state.select_points}/"
                        f"{max_points}"
                    )
                elif card_id not in selectable_ids:
                    text = f"卡牌 ID {card_id} 不在当前卡池的可选列表"
                else:
                    card = self._cards.by_id.get(card_id)
                    if card is None:
                        text = f"卡牌 ID {card_id} 不存在"
                    else:
                        receipt = self._db.claim_select_card(
                            user_id,
                            pool.pool_id,
                            card_id,
                            card.rarity,
                            max_select_points=max_points,
                        )
                        if receipt.success:
                            verb = "获得" if receipt.is_new else "重复获得"
                            text = (
                                f"天井兑换成功！{verb} "
                                f"{self._card_display_name(card, 40)}（ID {card.id}）\n"
                                f"当前持有：{receipt.copies} 张"
                            )
                        else:
                            text = receipt.error or "天井兑换失败"
        await self._send_lines(
            stream_id,
            text,
            force_forward=(action == "列表"),
        )
        return True, text, True

    @Command(
        "ongeki_ceiling_list",
        description="查看当前卡池天井选择卡列表",
        pattern=r"^/(?:天井列表|天井 列表|天井选择列表|天井选择 列表)\s*$",
        aliases=["/og天井列表", "/og 天井列表"],
    )
    async def handle_ceiling_list(
        self,
        stream_id: str = "",
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        """独立入口：只查看当前池可选天井卡的 ID 与角色。"""
        kwargs["matched_groups"] = {"action": "列表"}
        kwargs.pop("stream_id", None)
        return await self.handle_ceiling(stream_id, **kwargs)

    @Command("ongeki_odds", description="查看模拟器概率和保底规则", pattern=r"^/(?:概率|抽卡概率)\s*$", aliases=["/og概率", "/og 概率"])
    async def handle_odds(self, stream_id: str = "", **kwargs: dict[str, Any]) -> tuple[bool, str, bool]:
        """显示当前模拟权重。"""
        del kwargs
        config = self.config
        if self._pool is None:
            text = "插件尚未初始化完成，请检查日志"
            await self._send_text(stream_id, text)
            return True, text, True
        self._sync_active_pool()
        if self._pool is None:
            return True, "插件尚未初始化完成，请检查日志", True
        pool_info = (
            f"卡池类型：{self._pool_kind_label(self._pool.pool_kind)}｜UP 卡：{self._pool.featured_count} 张"
            + (
                f"｜天井选择：{self._pool.select_count} 张"
                if self._pool.select_count
                else ""
            )
            + (
                "（本期无 UP，候选为当期版本已有全部 R/SR/SSR）"
                if not self._pool.featured_count
                else f"（权重 ×{self._pool.pickup_multiplier}）"
            )
        )
        lines = [
            "音击抽卡模拟器（本地娱乐参考值，非官方概率）",
            f"当前卡池：{self._pool.pool_name[:60]}",
            pool_info,
            (
                f"常驻池：{self._regular_pool_instance.pool_name[:60]}"
                "（可 /抽卡 常驻 使用）"
                if self._regular_pool_instance is not None
                else "常驻池：未配置"
            ),
        ]
        if self._pool.select_count:
            lines.append("天井选择 ID 与角色：/天井列表（或 /天井 列表）")
        weights = self._pool.rarity_weights
        total_weight = sum(weight for _, weight in weights)
        for rarity, weight in weights:
            percentage = weight / total_weight * 100
            lines.append(f"{rarity_display(rarity)}：{weight}（约 {percentage:.1f}%）")
        lines.append(
            f"消耗：1 连 {config.economy.cost_1} 点 / "
            f"5 连 {config.economy.cost_5} 点 / "
            f"11 连 {config.economy.cost_11} 点"
        )
        lines.append(
            "保底：11 连至少 1 张 SR 或以上；"
            "5 连每用户每周首次至少 1 张 SR 或以上"
        )
        lines.append(
            "5 连保底重置：每周四 07:00（"
            + self._timezone_label(config.economy.tz_offset_hours)
            + "）"
        )
        text = "\n".join(lines)
        await self._send_lines(stream_id, text)
        return True, text, True

    @Command(
        "ongeki_rules",
        description="查看详细规则说明",
        pattern=r"^/(?:规则|详细规则|玩法说明)\s*$",
        aliases=["/og规则", "/og 规则", "/详细规则"],
    )
    async def handle_rules(
        self,
        stream_id: str = "",
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        """以转发消息形式发送详细规则。"""
        del kwargs
        await self._send_forward(stream_id, self._rules_text())
        return True, "详细规则已发送", True

    @Command("ongeki_help", description="显示插件帮助", pattern=r"^/(?:帮助|on帮助)\s*$", aliases=["/og帮助", "/og 帮助"])
    async def handle_help(self, stream_id: str = "", **kwargs: dict[str, Any]) -> tuple[bool, str, bool]:
        """显示帮助信息。"""
        del kwargs
        text = self._help_text()
        await self._send_text(stream_id, text)
        return True, text, True


def time_ns() -> int:
    """返回当前纳秒时间戳。"""
    return time.time_ns()


def create_plugin() -> OngekiGachaPlugin:
    """创建插件实例。"""
    return OngekiGachaPlugin()
