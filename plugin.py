"""ONGEKI 模拟抽卡插件入口。"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
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

from .starter_cards import STARTER_CARD_IDS
from . import render_theme
from .config_model import OngekiGachaPluginConfig
from .gacha_core import CardCollection, CardInfo, CardPool, load_cards, rarity_display
from .gacha_db import GachaDatabase
from .growth_catalog import GrowthCatalog
from .reward_catalog import RewardCatalog
from .growth_service import GrowthService
from .growth_commands import GrowthCommandsMixin, request_identity
from .voice_service import VoiceCatalog, VoiceService
from .gacha_pools import GachaSchedule, PoolCard, PoolEntry
from .gacha_render import GachaRenderer, OVERLAY_FILES, RenderCard
from .card_reveal import render_card_reveal
from .catalog_render import (
    OTHER_KEY,
    OTHER_LABEL,
    buckets as catalog_buckets,
    index_text as catalog_index_text,
    page_text as catalog_page_text,
    render_catalog_index,
    render_catalog_page,
)
from .character_names import resolve as resolve_character_name
from .item_render import render_item_gain
from .task_catalog import (
    CatalogSong,
    GAME_LABELS,
    KIND_LABELS,
    TaskSelection,
    download_cover,
    load_or_fetch_catalog,
)
from .task_render import TaskCardData, render_task_card
from .task_commands import TaskCommandsMixin
from .text_render import render_text_card


logger = logging.getLogger(__name__)
POOL_KIND_LABELS = {
    "regular": "常驻",
    "limited": "限定",
    "pickup": "UP",
    "attribute": "属性",
    "special": "特殊",
}

class OngekiGachaPlugin(GrowthCommandsMixin, TaskCommandsMixin, MaiBotPlugin):
    """ONGEKI 模拟抽卡插件。"""

    config_model = OngekiGachaPluginConfig

    def __init__(self) -> None:
        super().__init__()
        self._lock: asyncio.Lock = asyncio.Lock()
        self._reveal_lock = asyncio.Lock()
        self._cards: CardCollection | None = None
        self._pool: CardPool | None = None
        self._active_pool_entries: tuple[PoolEntry, ...] = ()
        self._active_pool_instances: dict[str, CardPool] = {}
        self._selected_pool_id: str = ""
        self._regular_pool: PoolEntry | None = None
        self._regular_pool_instance: CardPool | None = None
        self._schedule: GachaSchedule | None = None
        self._non_gacha_cards: tuple[CardInfo, ...] = ()
        self._db: GachaDatabase | None = None
        self._growth: GrowthService | None = None
        self._voice: VoiceService | None = None
        self._renderer: GachaRenderer | None = None
        self._cards_dir: Path | None = None
        self._card_info_path: Path | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._pool_image_locks: dict[str, asyncio.Lock] = {}
        self._task_catalog: list[CatalogSong] | None = None
        self._task_catalog_lock = asyncio.Lock()
        self._task_catalog_path: Path | None = None
        self._task_reset_task: asyncio.Task | None = None
        # 素材是可选内容：发布包不含卡面、字体与音频，缺失时整体回退文字。
        self._render_ready: bool = False
        self._card_images_ready: bool = False
        self._ui_assets_ready: bool = False

    async def on_load(self) -> None:
        """加载卡牌、初始化数据库和渲染器。"""
        try:
            self._initialize()
        except Exception:
            await self.on_unload()
            raise
        self._cleanup_render_cache()
        self._run_daily_maintenance()
        self._cleanup_task = asyncio.create_task(self._daily_render_cache_cleanup_loop())
        if self.config.task.enabled:
            self._task_reset_task = asyncio.create_task(
                self._daily_task_reset_loop()
            )
        self.ctx.logger.info("ONGEKI 模拟抽卡插件已加载")

    async def on_unload(self) -> None:
        """释放数据库连接。"""
        if self._db is not None:
            self._db.close()
        self._db = None
        self._growth = None
        self._voice = None
        self._reward_catalog = None
        self._reward_catalog = None
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        if self._task_reset_task is not None:
            self._task_reset_task.cancel()
            try:
                await self._task_reset_task
            except asyncio.CancelledError:
                pass
            self._task_reset_task = None
        self._cards_dir = None
        self._card_info_path = None
        self._cards = None
        self._pool = None
        self._active_pool_entries = ()
        self._active_pool_instances = {}
        self._selected_pool_id = ""
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
            ) = self._build_runtime(
                self.config,
                rotation_epoch=self._get_rotation_epoch(),
            )
            growth_catalog = self._build_growth_catalog(cards)
            voice_catalog = VoiceCatalog(Path(__file__).parent / "assets/growth")
            voice_catalog.validate_rewards(growth_catalog)
            reward_catalog = RewardCatalog(Path(__file__).parent / "assets/growth")
        except Exception as exc:
            self.ctx.logger.exception("ONGEKI 模拟抽卡配置热更新失败，保留旧资源: %s", exc)
            return
        async with self._lock:
            if self._db is not None:
                self._db.initialize_growth(cards, enabled=self.config.growth.enabled, rules=growth_catalog.rules)
                self._growth = GrowthService(self._db, growth_catalog)
                self._reward_catalog = reward_catalog
                if self._voice is None:
                    self._voice = self._build_voice_service(self._growth, voice_catalog)
                self._voice.growth = self._growth
                self._voice.catalog = voice_catalog
                self._voice.enabled = self.config.growth.enabled and self.config.growth.voice_enabled
                self._voice.automatic_enabled = self.config.growth.automatic_voice_enabled
                self._voice.apply_limits(
                    cooldown_seconds=self.config.growth.voice_cooldown_seconds,
                    minute_limit=self.config.growth.voice_minute_limit,
                    request_ttl_seconds=self.config.growth.voice_request_ttl_seconds,
                    inflight_limit=self.config.growth.voice_inflight_limit,
                    send_timeout_seconds=self.config.growth.voice_send_timeout_seconds,
                )
            self._cards_dir = cards_dir
            self._card_info_path = card_info_path
            self._cards = cards
            self._pool = pool
            self._regular_pool = regular_pool
            self._regular_pool_instance = regular_pool_instance
            self._schedule = schedule
            self._non_gacha_cards = self._non_gacha_cards_from_schedule(cards, schedule)
            self._renderer = renderer
            self._render_ready = render_theme.fonts_available()
            self._card_images_ready = self._card_data_quick_ready(
                cards_dir, card_info_path
            )
            self._ui_assets_ready = self._overlay_assets_ready()
            self._task_catalog = None
        self._active_pool_instances.clear()
        self._sync_active_pool()
        self.ctx.logger.info("ONGEKI 模拟抽卡配置已热更新")

    def _initialize(self) -> None:
        db_path = self.ctx.paths.data_dir / "ongeki_gacha.db"
        self._task_catalog_path = self.ctx.paths.data_dir / "task_catalog_merged.json"
        database = GachaDatabase(db_path)
        database.open()
        self._db = database
        self._ensure_rotation_epoch()
        (
            cards_dir,
            card_info_path,
            cards,
            pool,
            regular_pool,
            regular_pool_instance,
            renderer,
            schedule,
        ) = self._build_runtime(
            self.config,
            rotation_epoch=self._get_rotation_epoch(),
        )
        self._cards_dir = cards_dir
        self._card_info_path = card_info_path
        self._cards = cards
        self._pool = pool
        self._regular_pool = regular_pool
        self._regular_pool_instance = regular_pool_instance
        self._schedule = schedule
        self._non_gacha_cards = self._non_gacha_cards_from_schedule(cards, schedule)
        self._renderer = renderer
        self._render_ready = render_theme.fonts_available()
        self._card_images_ready = self._card_data_quick_ready(
            cards_dir, card_info_path
        )
        self._ui_assets_ready = self._overlay_assets_ready()
        self._log_asset_state()
        self._task_catalog = None
        catalog = self._build_growth_catalog(cards)
        voice_catalog = VoiceCatalog(Path(__file__).parent / "assets/growth")
        voice_catalog.validate_rewards(catalog)
        self._reward_catalog = RewardCatalog(Path(__file__).parent / "assets/growth")
        database.initialize_growth(cards, enabled=self.config.growth.enabled, rules=catalog.rules)
        self._growth = GrowthService(database, catalog)
        self._voice = VoiceService(
            self._growth,
            voice_catalog,
            self.ctx.send,
            enabled=self.config.growth.enabled and self.config.growth.voice_enabled,
            automatic_enabled=self.config.growth.automatic_voice_enabled,
            cooldown_seconds=self.config.growth.voice_cooldown_seconds,
            minute_limit=self.config.growth.voice_minute_limit,
            request_ttl_seconds=self.config.growth.voice_request_ttl_seconds,
            inflight_limit=self.config.growth.voice_inflight_limit,
            send_timeout_seconds=self.config.growth.voice_send_timeout_seconds,
        )
        self._sync_active_pool()

    def _build_growth_catalog(self, cards: CardCollection) -> GrowthCatalog:
        """随包规则 + 插件配置覆盖；具名配置优先于 rules_overrides。"""
        return GrowthCatalog(
            Path(__file__).parent / "assets/growth",
            cards,
            rules_override=self.config.growth.rule_overrides(),
        )

    def _build_voice_service(self, growth: GrowthService, voice_catalog: VoiceCatalog) -> VoiceService:
        return VoiceService(
            growth,
            voice_catalog,
            self.ctx.send,
            enabled=self.config.growth.enabled and self.config.growth.voice_enabled,
            automatic_enabled=self.config.growth.automatic_voice_enabled,
            cooldown_seconds=self.config.growth.voice_cooldown_seconds,
            minute_limit=self.config.growth.voice_minute_limit,
            request_ttl_seconds=self.config.growth.voice_request_ttl_seconds,
            inflight_limit=self.config.growth.voice_inflight_limit,
            send_timeout_seconds=self.config.growth.voice_send_timeout_seconds,
        )

    def _build_runtime(
        self,
        config: OngekiGachaPluginConfig,
        *,
        rotation_epoch: date | None = None,
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
        active_pool_entries = self._active_pool_entries_for(
            schedule,
            config,
            epoch=rotation_epoch,
        )
        active_pool = active_pool_entries[0] if active_pool_entries else None
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

    def _ensure_rotation_epoch(self) -> None:
        if self._db is None:
            return
        configured = str(self.config.pool.rotation_epoch or "").strip()
        if configured:
            try:
                date.fromisoformat(configured)
            except ValueError:
                self.ctx.logger.warning("pool.rotation_epoch 无效，忽略: %s", configured)
            else:
                self._db.set_setting("pool_rotation_epoch", configured)
                return
        if self._db.get_setting("pool_rotation_epoch"):
            return
        today = self._today(self.config)
        self._db.set_setting("pool_rotation_epoch", today.isoformat())

    def _get_rotation_epoch(self) -> date:
        if self._db is None:
            return date.min
        configured = str(self.config.pool.rotation_epoch or "").strip()
        if configured:
            try:
                return date.fromisoformat(configured)
            except ValueError:
                pass
        raw = self._db.get_setting("pool_rotation_epoch")
        if raw:
            try:
                return date.fromisoformat(raw)
            except ValueError:
                pass
        today = self._today(self.config)
        self._db.set_setting("pool_rotation_epoch", today.isoformat())
        return today

    @classmethod
    def _schedule_pool(
        cls,
        schedule: GachaSchedule,
        config: OngekiGachaPluginConfig,
        epoch: date | None = None,
    ) -> PoolEntry | None:
        """Select the active pool according to the configured rotation mode."""
        if not schedule.entries:
            return None
        mode = str(config.pool.rotation_mode or "cycle").strip().lower()
        today = cls._today(config)
        if mode == "official":
            return schedule.active_for(today, ignore_year=True)
        return schedule.cycle_for(
            today,
            config.pool.rotation_interval_days,
            epoch=epoch,
        )

    @classmethod
    def _active_pool_entries_for(
        cls,
        schedule: GachaSchedule,
        config: OngekiGachaPluginConfig,
        epoch: date | None = None,
    ) -> tuple[PoolEntry, ...]:
        """Return every pool currently active under the configured mode."""
        if not schedule.entries:
            return ()
        mode = str(config.pool.rotation_mode or "cycle").strip().lower()
        if mode == "official":
            return schedule.active_for_all(
                cls._today(config),
                ignore_year=True,
            )
        active = cls._schedule_pool(schedule, config, epoch=epoch)
        return (active,) if active is not None else ()

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
            if card.id not in featured_ids and card.id not in STARTER_CARD_IDS
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
            if card_id in cards.by_id and card_id not in STARTER_CARD_IDS
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
        configured_ready = cls._card_data_json_ready(
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

        default_ready = cls._card_data_json_ready(
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
            "卡牌索引不可用。请运行 tools/sync_card_data.py 生成 card_info_merged.json，"
            "或在配置 assets.cards_dir / assets.card_info_json 中填写有效绝对路径；"
            "卡面图片可稍后用 tools/card_asset_tools.py 接入。"
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
    def _card_data_json_ready(cards_dir: Path, card_info_path: Path) -> bool:
        """数据就绪判断：只要求卡牌索引 JSON 可用，卡面图片允许后补。

        发布包只带索引，卡面由用户用素材工具接入；因此加载不再要求 PNG 存在。
        """
        if not cards_dir.is_dir() or not card_info_path.is_file():
            return False
        try:
            rows = json.loads(card_info_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return False
        if not isinstance(rows, list):
            return False
        return any(
            isinstance(row, dict) and bool(row.get("imagePresent", False))
            for row in rows
        )

    @staticmethod
    def _card_data_quick_ready(cards_dir: Path, card_info_path: Path) -> bool:
        """卡面是否全部就位：索引 JSON 与它引用的每张 PNG 都存在。"""
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

    def _log_asset_state(self) -> None:
        """记录素材可用性；发布包不含素材时给出可操作的提示。"""
        if self._render_ready and self._card_images_ready and self._ui_assets_ready:
            return
        missing = []
        if not self._render_ready:
            missing.append("可用字体")
        if not self._card_images_ready:
            missing.append("卡面")
        if not self._ui_assets_ready:
            missing.append("界面素材")
        logger.warning(
            "未检测到%s，相关图片输出会回退为文字；"
            "可在插件目录用 tools/ 下的素材工具接入后重载插件。",
            "与".join(missing),
        )

    @staticmethod
    def _overlay_assets_ready() -> bool:
        """抽卡结果图所需的原作界面素材是否齐全。"""
        ui_dir = Path(__file__).parent / "assets" / "ui"
        return all((ui_dir / name).is_file() for name in OVERLAY_FILES.values())

    def _help_text(self) -> str:
        """按当前配置生成 /帮助 返回文案。"""
        return (
            "【签到与资产】\n"
            "/签到　/点数　/月卡 [查看|购买|续费]\n"
            "/卡册 <角色姓名> [页码]　/卡图 <卡ID>\n"
            "【抽卡与卡池】\n"
            "/抽卡 [常驻|<池ID>] [1|5|11]　/概率\n"
            "/卡池 [列表|下一期]　/天井　/天井列表\n"
            "/天井池 <池ID> <卡ID>（同卡属于多个启用池时指定）\n"
            "【养成】\n"
            "/伙伴 <角色姓名>　/陪伴　/好感 [角色姓名|列表]\n"
            "/好感 <角色姓名> 卡面 <卡ID>　/好感奖励 [角色姓名]\n"
            "/礼物 [购买 小|中]　/送礼 <角色姓名> 小|中|大 [数量]\n"
            "/养成 <卡ID>　/解花 <卡ID>　/超解花 <卡ID>\n"
            "/装扮 [称号|装饰 <ID>|卸下]\n"
            "/角色语音 [角色姓名] [序号]　/角色语音 分类\n"
            "【随机任务】\n"
            "/接任务 普通|挑战|高级挑战|终极 [音击|舞萌|中二]　/任务列表\n"
            "/任务完成 <任务ID>（同时发送成绩图）\n"
            "【管理员】\n"
            "/任务审核 <任务ID> <S|SS|SSS|SSS+|拒绝>　/任务审核列表\n"
            "/终极完成 <用户> <任务ID>　/奖励 <用户> <点数>\n"
            "/任务重置 <任务ID>　/任务清理 [天数]\n"
            "【说明】\n"
            "角色姓名默认显示原作写法，常用中文写法同样可用。\n"
            "示例：/抽卡 常驻 11　/卡册 星咲 あかり 1　"
            "/好感 星咲 あかり　/角色语音 星咲 あかり 1\n"
            "详细数值与规则发送 /规则"
        )

    def _rules_text(self) -> list[str]:
        """生成完整规则说明，供转发消息使用。"""
        economy = self.config.economy
        monthly = self.config.monthly_card
        item_labels = {
            "gift_small": "小礼物",
            "gift_medium": "中礼物",
            "gift_large": "大礼物",
            "flower_fragment": "花之碎片",
            "bloom_ticket": "解花券",
        }
        bloom_items = list(self.config.growth.bloom_items)
        bloom_labels = [
            item_labels.get(item, item) for item in bloom_items
        ]
        return [
            "【主角色养成】",
            f"养成状态：{'已启用' if self.config.growth.enabled else '暂停'}；17名主角色默认开放，"
            "基础N卡第一张在首次查看该角色好感页时获得并发送揭示图；之后10张由好感节点发放至11星；角色姓名默认按原作写法显示，常用中文写法同样可用",
            "/好感 [角色姓名] 打开好感页，/好感 列表 查看17名角色的好感等级；"
            "卡面用 /好感 <角色姓名> 卡面 <卡ID>，示例 /好感 星咲 あかり 卡面 100008",
            "17名主角色默认开放，不需要先抽到角色卡；用 /伙伴 <角色姓名> 选择伙伴，"
            "/陪伴 每个账号每天一次，按UTC日期结算",
            "/装扮 查看已解锁称号与装饰，可用 /装扮 称号/装饰 <ID> 装备或卸下",
            "新获得的称号与装饰会自动装备，同类型更高节点覆盖为最新一件；仍可在 /装扮 中换装或卸下",
            "/卡册 <角色姓名> [页码] 按角色查看卡册与卡ID，非主角色归入“其他”；"
            "翻页由用户指定页码，单次只发一页，示例 /卡册 星咲 あかり 1",
            "礼物价值：小礼物300、中礼物1000、大礼物10000好感；好感无上限，"
            "10档（Lv1000）后按最高档位的每级需求继续累计，心形最多显示 99 / 99，累计点数不封顶",
            "重复卡立即折算花之碎片，超出满星后折算更多；解花不要求卡牌满星，"
            "超解花需满星（N卡11星、其他5星）且已解花；"
            f"角色好感分别达到 Lv{self.config.growth.bloom_levels[0]} / "
            f"Lv{self.config.growth.bloom_levels[1]}，"
            f"解花消耗 {self.config.growth.bloom_costs[0]} {bloom_labels[0]}，"
            f"超解花消耗 {self.config.growth.bloom_costs[1]} {bloom_labels[1]}"
            "（这里的等级是角色好感等级，与卡面等级无关）",
            "解花与超解花成功后会发送阶段对照图；卡ID可在 /卡册 或抽卡结果图底部查看",
            (
                f"解花券来源：高级挑战目标定数 ≥ "
                f"{self.config.growth.bloom_ticket_source_min_level:g} 且以 "
                f"{self.config.growth.bloom_ticket_source_grade} 及以上通关时获得 1 张{bloom_labels[0]}，"
                f"获得后 {self.config.growth.bloom_ticket_cooldown_days} 天冷却；"
                "花之碎片只用于超解花，归属未确认的卡暂不可新解花"
            ),
            "旧版解花阶段继承；好感奖励自动领取，奖励N卡不产生重复碎片",
            "每月1-7日为签到活动：第1、3天小礼物，第5天中礼物，第7天大礼物，其余活动日发花之碎片；活动之外的签到不发养成物品",
            f"签到活动日的碎片奖励为每次 {self.config.growth.monthly_event_fragments}；"
            f"任务审核每次1碎片，每天合计最多{self.config.growth.task_fragments_daily_cap}；"
            "好感节点发的N卡不产生碎片",
            "小礼物与中礼物可用点数购买（每周限量，见 /礼物），"
            "中礼物还可由挑战/高级挑战任务审核获得，每日最多1份；终极任务只发放点数",
            "语音分为自动回应与手动点播：小礼物、中/大礼物、好感升级会自动回应一条，升级优先；"
            "档案语音在 /角色语音 <角色姓名> <序号> 手动播放，可用 /角色语音 分类 查看分类",
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
                f"{economy.savings_bonus_3} 点；每 "
                f"{economy.savings_bonus_reset_days} 天重置档位"
            ),
            "【抽卡】",
            f"消耗：1 连 {economy.cost_1} 点、5 连 {economy.cost_5} 点、11 连 {economy.cost_11} 点",
            "抽卡流程：先按稀有度权重，再在池内按卡权重抽取，UP 卡按配置倍率加权",
            "11 连必得 SR 或以上；5 连每用户每周首次触发一次 SR 或以上保底；"
            "抽卡次数与保底按用户独立累计",
            "【卡池】",
            f"默认每 {self.config.pool.rotation_interval_days} 天轮换一个历史官方卡池；"
            "official 模拟模式按几月几日匹配历史官方池（忽略年份）",
            "official 模式可能同时启用多个官方卡池；默认抽最近开启的活动池，"
            "可用 /抽卡 <池ID> <数量> 指定其他启用池",
            "/卡池 会列出全部启用中的官方卡池，/天井列表 会列出全部启用池的天井状态",
            "活动池候选为当期版本已有全部 R/SR/SSR；官方公告未写 UP 时会显示 "
            "UP 卡：0 张，但仍会抽取当期版本的基础卡",
            "常驻池包含当前版本已有的全部 R/SR/SSR 基础卡，可用 /抽卡 常驻 单独抽取",
            "【天井】",
            "抽卡每张 +1 点天井点；达到上限后可 /天井 <卡ID> 兑换默认池的可选卡，"
            "也可用 /天井池 <池ID> <卡ID> 指定其他启用池",
            "/天井列表可查看全部当前启用池的天井进度与可选卡",
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
            "管理员可通过 /奖励 @用户 <点数> [备注] 向用户发放点数，"
            "发放与接收记录都按用户写入审计日志",
            "【随机任务】",
            "不指定游戏时默认三游戏全随机，也可 /接任务 <类型> <音击|舞萌|中二> 指定游戏；"
            "任务次数按用户独立计算",
            f"普通任务：每日 {self.config.task.normal_count} 次，任意难度，奖励 {self.config.task.normal_reward} 点",
            (
                f"挑战任务：每日 {self.config.task.challenge_count} 次，"
                f"从至少有一张 {self.config.task.challenge_min_level:g} "
                "级或以上谱面的歌曲中随机，并选取该曲的最低达标谱面，"
                f"要求该谱面或以上 S 评级；是否挑战更高难度由玩家选择，"
                "不锁定曲目最高难度"
            ),
            (
                f"挑战奖励：S {self.config.task.challenge_reward_s} 点、"
                f"SS {self.config.task.challenge_reward_ss} 点、"
                f"SSS/SSS+ {self.config.task.challenge_reward_sss} 点"
            ),
            (
                f"高级挑战：每日 {self.config.task.advanced_count} 次，"
                f"从至少有一张定数 {self.config.task.advanced_min_level:g} "
                "或以上谱面的歌曲中随机，并选取该曲的最低达标谱面，"
                "要求该谱面或以上 S 评级；奖励 "
                f"S {self.config.task.advanced_reward_s} 点、"
                f"SS {self.config.task.advanced_reward_ss} 点、"
                f"SSS/SSS+ {self.config.task.advanced_reward_sss} 点"
            ),
            (
                f"高级挑战目标定数 ≥ "
                f"{self.config.growth.bloom_ticket_source_min_level:g} 且以 "
                f"{self.config.growth.bloom_ticket_source_grade} 及以上通关时，"
                f"额外获得 1 张{bloom_labels[0]}；获得后 "
                f"{self.config.growth.bloom_ticket_cooldown_days} 天冷却，冷却期间不再产出"
            ),
            (
                f"终极任务：从谱面定数 ≥ {self.config.task.ultimate_min_level:g}"
                "（这是内部定数阈值，不是 14 级+）"
                "的超高难谱面随机，要求 SSS+ 评级，"
                f"奖励 {self.config.task.ultimate_reward} 点；"
                "同一曲目若有多个达标难度，会作为不同任务分别记录，"
                "已完成的难度不再重复，全部达标谱面完成后该线结束"
            ),
            "/任务完成 <任务ID> 需同时发送成绩照片，提交后请管理员审核",
            (
                f"普通/挑战/高级挑战未完成任务将在每日 00:00 自动过期；"
                "待审核任务保留，已结束任务默认 "
                f"{self.config.task.task_history_retention_days} 天后自动清理"
            ),
            "管理员可发送 /任务清理 [天数] 立即清理已结束任务，待审核任务不会被删除",
            "【说明】",
            "签到、任务、周保底与轮替均按国际时间 UTC 计算",
            "以上奖励与周期均可在插件配置中调整，详细说明见 USAGE.md",
        ]

    @staticmethod
    def _monthly_card_remaining_days(expires_at: str, tz_offset_hours: int) -> int:
        """按统一的 UTC 日期计算月卡剩余天数。"""
        today = GachaDatabase.current_date_str(tz_offset_hours)
        return GachaDatabase.monthly_card_remaining_days(expires_at, today)

    @staticmethod
    def _timezone_label(offset_hours: int) -> str:
        """把 UTC 偏移小时数显示为 UTC 或 UTC±N。"""
        del offset_hours
        return "UTC"

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

    @staticmethod
    def _has_photo(kwargs: dict[str, Any]) -> bool:
        """判断消息是否包含图片段。"""
        message = kwargs.get("message")
        if not isinstance(message, dict):
            return False
        raw = message.get("raw_message")
        if not isinstance(raw, list):
            return False
        for component in raw:
            if not isinstance(component, dict):
                continue
            text_type = str(component.get("type") or "").strip().lower()
            if text_type == "image":
                return True
            if any(
                component.get(key)
                for key in ("image_base64", "image_url", "url", "file", "path")
            ):
                return True
        return False

    @staticmethod
    def _task_kind_from_kwargs(kwargs: dict[str, Any]) -> str:
        groups = kwargs.get("matched_groups")
        if isinstance(groups, dict):
            raw = str(groups.get("kind") or "").strip()
            mapping = {
                "普通": "normal",
                "挑战": "challenge",
                "高级挑战": "advanced",
                "终极": "ultimate",
            }
            if raw in mapping:
                return mapping[raw]
        text = str(kwargs.get("text") or "")
        if "终极" in text:
            return "ultimate"
        if "高级挑战" in text:
            return "advanced"
        if "挑战" in text:
            return "challenge"
        return "normal"

    @staticmethod
    def _normalize_grade(raw: str) -> str:
        value = str(raw or "").strip().upper().replace("＋", "+")
        aliases = {
            "普通": "普通",
            "通过": "普通",
            "OK": "普通",
            "PASS": "普通",
            "S": "S",
            "SS": "SS",
            "SSS": "SSS",
            "SSS+": "SSS+",
            "SSSP": "SSS+",
            "拒绝": "拒绝",
            "REJECT": "拒绝",
            "NO": "拒绝",
        }
        return aliases.get(value, value)

    def _task_reward(self, task_kind: str, grade: str = "") -> int:
        config = self.config.task
        if task_kind == "normal":
            return config.normal_reward
        if task_kind == "challenge":
            grade = self._normalize_grade(grade or "S")
            if grade in {"S", "普通"}:
                return config.challenge_reward_s
            if grade == "SS":
                return config.challenge_reward_ss
            if grade in {"SSS", "SSS+"}:
                return config.challenge_reward_sss
        if task_kind == "advanced":
            grade = self._normalize_grade(grade or "S")
            if grade in {"S", "普通"}:
                return config.advanced_reward_s
            if grade == "SS":
                return config.advanced_reward_ss
            if grade in {"SSS", "SSS+"}:
                return config.advanced_reward_sss
        if task_kind == "ultimate":
            return config.ultimate_reward
        return 0

    @staticmethod
    def _task_level_text(selection: TaskSelection) -> str:
        return selection.level_text

    def _task_requirement(self, selection: TaskSelection, task_kind: str) -> str:
        if task_kind == "normal":
            return "任意难度"
        grade_text = "SSS+" if task_kind == "ultimate" else "S 及以上"
        if selection.chart is None:
            return grade_text
        requirement = f"{selection.requirement} · {grade_text}"
        if (
            task_kind == "advanced"
            and selection.chart.level_value
            >= float(self.config.growth.bloom_ticket_source_min_level)
        ):
            requirement += f"；{self.config.growth.bloom_ticket_source_grade} 及以上可获解花券"
        return requirement

    def _task_sources(self) -> tuple[dict[str, str], dict[str, str]]:
        task = self.config.task
        sources = {
            "ongeki": task.ongeki_source_url,
            "maimai": task.maimai_song_url,
            "chunithm": task.chunithm_song_url,
        }
        assets = {
            "ongeki": task.ongeki_source_url,
            "maimai": task.maimai_asset_url,
            "chunithm": task.chunithm_asset_url,
        }
        return sources, assets

    async def _get_task_catalog(self) -> list[CatalogSong] | None:
        if self._task_catalog is not None:
            return self._task_catalog
        async with self._task_catalog_lock:
            if self._task_catalog is not None:
                return self._task_catalog
            if self._task_catalog_path is None:
                self._task_catalog_path = self.ctx.paths.data_dir / "task_catalog_merged.json"
            sources, assets = self._task_sources()
            try:
                catalog = await asyncio.to_thread(
                    load_or_fetch_catalog,
                    self._task_catalog_path,
                    ttl=self.config.task.catalog_cache_ttl,
                    sources=sources,
                    asset_bases=assets,
                )
            except Exception as exc:
                self.ctx.logger.warning("获取任务曲库失败: %s", exc)
                return None
            self._task_catalog = catalog
            return catalog

    async def _render_task_card(
        self,
        task_id: int,
        selection: TaskSelection,
        task_kind: str,
        user_id: str,
    ) -> tuple[str | None, str, bool]:
        """返回 (图片 base64, 文本信息, 卡片是否完整)。"""
        chart = selection.chart
        text = self._task_card_text(task_id, selection, task_kind)
        if not self._render_ready:
            text += "\n（当前未接入渲染素材，任务以文字模式发送）"
            return None, text, False
        cover_path = None
        cover_loaded = False
        if selection.song.cover_url:
            import hashlib

            key = hashlib.sha1(
                f"{selection.song.game}:{selection.song.song_id}".encode("utf-8")
            ).hexdigest()[:16]
            cover_path = self.ctx.paths.runtime_dir / "task_covers" / f"{key}.png"
            if cover_path.is_file() and cover_path.stat().st_size > 0:
                cover_loaded = True
            else:
                cover_urls = [selection.song.cover_url]
                if selection.song.game in {"maimai", "chunithm"}:
                    fallback = (
                        selection.song.cover_url.replace(
                            "assets.lxns.net",
                            "assets2.lxns.net",
                        )
                        .replace(".png!webp", ".png")
                    )
                    if fallback not in cover_urls:
                        cover_urls.append(fallback)
                for cover_url in cover_urls:
                    ok = await asyncio.to_thread(
                        download_cover,
                        cover_url,
                        cover_path,
                    )
                    if ok:
                        cover_loaded = True
                        break
                if not cover_loaded:
                    cover_path = None

        card_data = TaskCardData(
            task_id=task_id,
            kind=task_kind,
            game=selection.song.game,
            title=selection.song.title,
            artist=selection.song.artist,
            level=self._task_level_text(selection),
            requirement=self._task_requirement(selection, task_kind),
            reward=self._task_reward(task_kind),
            user_id=user_id,
            note="完成后请发送对应成绩截图",
            cover_path=cover_path,
        )
        output_path = self.ctx.paths.runtime_dir / f"ongeki_task_{task_id}.png"
        try:
            await asyncio.to_thread(render_task_card, card_data, output_path)
            image_base64 = base64.b64encode(output_path.read_bytes()).decode("ascii")
        except Exception as exc:
            self.ctx.logger.warning("任务卡渲染失败: %s", exc)
            image_base64 = None
        if selection.song.cover_url and not cover_loaded:
            text += "\n（曲绘加载失败，任务信息已返回文字模式）"
        elif image_base64 is None:
            text += "\n（任务卡图片生成失败，已返回文字模式）"
        complete = cover_loaded and image_base64 is not None
        return image_base64, text, complete

    def _task_card_text(
        self,
        task_id: int,
        selection: TaskSelection,
        task_kind: str,
    ) -> str:
        """任务信息的纯文字版本；渲染不可用时直接发送这段内容。"""
        game_label = GAME_LABELS.get(selection.song.game, selection.song.game)
        return (
            f"任务ID：#{task_id}\n"
            f"类型：{KIND_LABELS.get(task_kind, task_kind)}\n"
            f"游戏：{game_label}\n"
            f"曲目：{selection.song.title} — {selection.song.artist}\n"
            f"任务谱面：{self._task_level_text(selection)}\n"
            f"要求：{self._task_requirement(selection, task_kind)}\n"
            f"奖励：{self._task_reward(task_kind)} 点\n"
            f"提交：/任务完成 {task_id}（同时发送成绩图）"
        )

    def _cleanup_render_cache(self) -> None:
        """删除超过保留期的一次性渲染图片。

        运行目录中的抽卡、文本卡片、任务卡、揭示图、物品图、卡册页与好感页
        都是一次性产物，按 ``[ui] render_cache_ttl_seconds`` 清理；卡池公告图
        （``ongeki_pool_*``）与曲绘缓存（``task_covers/``）有各自的 TTL，
        不在此处删除。
        """
        runtime_dir = self.ctx.paths.runtime_dir
        if not runtime_dir.is_dir():
            return
        cutoff = time.time() - self.config.ui.render_cache_ttl_seconds
        removed = []
        for pattern in (
            "ongeki_draw_*.png",
            "ongeki_text_*.png",
            "ongeki_task_*.png",
            "ongeki_reveal_*.png",
            "ongeki_item_*.png",
            "ongeki_catalog_*.png",
            "growth_*.png",
        ):
            for path in runtime_dir.glob(pattern):
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed.append(str(path))
                except OSError as exc:
                    logger.warning("清理临时图片失败 %s: %s", path, exc)
        if removed:
            logger.info("已清理 %d 张过期临时图片", len(removed))

    async def _daily_render_cache_cleanup_loop(self) -> None:
        """Run the daily cleanup loop until the plugin is unloaded."""
        try:
            while True:
                await asyncio.sleep(self.config.ui.render_cache_ttl_seconds)
                self._cleanup_render_cache()
        except asyncio.CancelledError:
            raise

    async def _daily_task_reset_loop(self) -> None:
        """每日按真实日期同步状态，并在 00:00 后自动维护任务。"""
        try:
            while True:
                self._run_daily_maintenance()
                now = datetime.now(timezone.utc)
                next_day = now + timedelta(days=1)
                next_midnight = next_day.replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                await asyncio.sleep(
                    max(1.0, (next_midnight - now).total_seconds())
                )
        except asyncio.CancelledError:
            raise

    def _run_daily_maintenance(self) -> None:
        """按真实日期执行一次持久化维护，重启后也不会漏掉跨日状态。"""
        if self._db is None:
            return
        today = GachaDatabase.current_date_str(
            self.config.economy.tz_offset_hours
        )
        try:
            if self._db.get_setting("last_daily_maintenance_date") == today:
                return
            expired = 0
            cleaned = 0
            quota_cleaned = 0
            if self.config.task.enabled and self.config.task.auto_reset:
                expired = self._db.expire_daily_tasks(today)
            if (
                self.config.task.enabled
                and self.config.task.auto_cleanup_history
            ):
                cleaned = self._db.cleanup_task_history(
                    today,
                    retention_days=self.config.task.task_history_retention_days,
                )
                quota_cleaned = self._db.cleanup_daily_task_quota(
                    today,
                    retention_days=self.config.task.task_history_retention_days,
                )
            weekly, savings = self._db.sync_time_based_state(
                tz_offset_hours=self.config.economy.tz_offset_hours,
                savings_bonus_reset_days=(
                    self.config.economy.savings_bonus_reset_days
                ),
            )
            self._db.set_setting(
                "last_daily_maintenance_date",
                today,
            )
            if expired:
                self.ctx.logger.info("每日任务自动过期：%d 条", expired)
            if cleaned or quota_cleaned:
                self.ctx.logger.info(
                    "每日任务历史清理：%d 条任务，%d 条配额",
                    cleaned,
                    quota_cleaned,
                )
            if weekly or savings:
                self.ctx.logger.info(
                    "时间状态同步：%d 个周保底，%d 个囤点周期",
                    weekly,
                    savings,
                )
        except Exception as exc:
            self.ctx.logger.warning("每日任务维护失败: %s", exc)

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
    def _draw_pool_selector(kwargs: dict[str, Any]) -> str:
        """Return an explicit active pool selector, or empty for default."""
        groups = kwargs.get("matched_groups")
        if not isinstance(groups, dict):
            return ""
        raw = str(groups.get("pool") or "").strip()
        if raw in {"1", "5", "11", "常驻", "普通", "常规"}:
            return ""
        return raw

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

    @staticmethod
    def _action_from_kwargs(kwargs: dict[str, Any], allowed: tuple[str, ...]) -> str:
        """从命名捕获组或完整命令文本中解析动作，兼容别名命令。"""
        groups = kwargs.get("matched_groups")
        if isinstance(groups, dict):
            raw = str(groups.get("action") or "").strip()
            if raw in allowed:
                return raw
        text = str(kwargs.get("text") or "")
        for action in allowed:
            if action in text:
                return action
        return ""

    def _cost(self, count: int) -> int:
        config = self.config.economy
        return {
            1: config.cost_1,
            5: config.cost_5,
            11: config.cost_11,
        }.get(count, 0)

    def _sync_active_pool(self, requested_pool_id: str = "") -> bool:
        """Refresh active pools and select one for the default draw pool."""
        if self._schedule is None or self._cards is None:
            return False
        active_entries = self._active_pool_entries_for(
            self._schedule,
            self.config,
            self._get_rotation_epoch(),
        )
        self._active_pool_entries = active_entries
        selected: PoolEntry | None = None
        requested = str(requested_pool_id or "").strip().lower()
        if requested:
            for entry in active_entries:
                pool_key = entry.pool_id.lower()
                if pool_key == requested or pool_key.endswith("-" + requested):
                    selected = entry
                    break
            if selected is None:
                self._selected_pool_id = ""
                self._pool = None
                return False
        if selected is None and active_entries:
            selected = max(
                active_entries,
                key=lambda item: item.start_date or date.min,
            )
        if selected is None:
            self._selected_pool_id = ""
            self._pool = None
            return False

        self._selected_pool_id = selected.pool_id
        pool = self._active_pool_instances.get(selected.pool_id)
        if pool is None:
            pool = CardPool(
                self._cards,
                weight_n=self.config.pool.weight_n,
                weight_r=self.config.pool.weight_r,
                weight_sr=self.config.pool.weight_sr,
                weight_sr_plus=self.config.pool.weight_sr_plus,
                weight_ssr=self.config.pool.weight_ssr,
                pool=selected,
                pickup_multiplier=self.config.pool.pickup_multiplier,
                strict_pool_cards=(
                    self.config.pool.strict_pool_cards
                    or selected is not None
                ),
            )
            self._active_pool_instances[selected.pool_id] = pool
        self._pool = pool
        return True

    def _cycle_index(self, schedule: GachaSchedule) -> int | None:
        if not schedule.entries:
            return None
        interval = max(int(self.config.pool.rotation_interval_days), 1)
        today = self._today(self.config)
        epoch = self._get_rotation_epoch()
        elapsed_days = max((today - epoch).days, 0)
        return (elapsed_days // interval) % len(schedule.entries)

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

    def _download_pool_image(self, url: str) -> bytes:
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
            data = response.read(self.config.ui.max_pool_image_bytes + 1)
        if len(data) > self.config.ui.max_pool_image_bytes:
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
            image_lock = self._pool_image_locks.setdefault(cache_name, asyncio.Lock())
            async with image_lock:
                if cache_path.is_file():
                    age = time.time() - cache_path.stat().st_mtime
                    if age <= self.config.ui.pool_image_cache_ttl_seconds:
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

    async def _send_text(
        self,
        stream_id: str,
        text: str,
        *,
        title: str = "音击抽卡模拟器",
    ) -> bool:
        """短回复直接发文字，长内容渲染分页图片，失败时回退纯文本。

        返回是否以纯文本形式送达：调用方据此决定要不要再补发一份可复制的
        指令提示，避免同一批内容发两遍。
        """
        lines = str(text or "").splitlines()
        if not any(line.strip() for line in lines):
            return False
        if self._is_short_reply(lines, str(text or "")):
            await self._send_plain_text(stream_id, text)
            return True
        if await self._send_text_card(stream_id, title, lines):
            return False
        await self._send_plain_text(stream_id, text)
        return True

    async def _send_text_card(
        self,
        stream_id: str,
        title: str,
        lines: list[str],
    ) -> bool:
        """把长文本渲染成图片发送；成功返回 True。"""
        if not any(str(line).strip() for line in lines):
            return False
        if not self._render_ready:
            return False
        output_path = (
            self.ctx.paths.runtime_dir / f"ongeki_text_{time_ns()}.png"
        )
        try:
            paths = await asyncio.to_thread(
                render_text_card,
                title,
                lines,
                output_path,
            )
        except Exception as exc:
            self.ctx.logger.warning("长文本图片渲染失败，回退文本发送: %s", exc)
            return False
        if not paths:
            return False
        for path in paths:
            try:
                image_base64 = base64.b64encode(
                    path.read_bytes()
                ).decode("ascii")
                result = await self.ctx.send.image(image_base64, stream_id)
                if result is False or (
                    isinstance(result, dict)
                    and (result.get("sent") is False or result.get("success") is False)
                ):
                    self.ctx.logger.warning("长文本图片发送未成功，回退完整文本")
                    return False
            except Exception as exc:
                self.ctx.logger.warning("长文本图片发送失败: %s", exc)
                return False
        return True

    def _is_short_reply(self, lines: list[str], text: str) -> bool:
        """短回执直接发文字，只有长列表与规则才排版成图片卡片。"""
        content = [line for line in lines if str(line).strip()]
        return (
            len(content) <= self.config.ui.short_reply_max_lines
            and len(text) <= self.config.ui.short_reply_max_chars
        )

    async def _send_plain_text(self, stream_id: str, text: str) -> None:
        """发送纯文本回执；发送失败只记录日志，不改变指令结果。"""
        try:
            await self.ctx.send.text(text, stream_id)
        except Exception as exc:
            self.ctx.logger.warning("文字回复发送失败: %s", exc)

    async def _send_checkin_bonus_card(
        self,
        stream_id: str,
        card: CardInfo,
        copies: int,
        is_kaika: bool,
        is_cho_kaika: bool,
        is_new: bool = False,
    ) -> None:
        """渲染并发送签到彩蛋卡的揭示图。"""
        await self._send_card_reveal(
            stream_id,
            card,
            copies,
            mode="new" if is_new or copies <= 1 else "star_up",
            before_copies=max(0, int(copies) - 1),
            is_kaika=is_kaika,
            is_cho_kaika=is_cho_kaika,
        )

    async def _send_card_reveal(
        self,
        stream_id: str,
        card: CardInfo,
        copies: int,
        *,
        mode: str = "new",
        before_copies: int = 0,
        is_kaika: bool = False,
        is_cho_kaika: bool = False,
    ) -> bool:
        """发送原作风格卡牌揭示图；返回已确认发送的状态。"""
        if self._renderer is None or not self._render_ready:
            return False
        if not self._card_images_ready or not self._ui_assets_ready:
            # 卡面未接入时揭示图只会画出占位图，直接回退文字。
            return False
        character_name = ""
        if self._growth is not None:
            character = self._growth.catalog.characters.get(card.character_id)
            if character is not None:
                character_name = str(character.get("name") or "")
        output_path = (
            self.ctx.paths.runtime_dir
            / f"ongeki_reveal_{card.id}_{time_ns()}.png"
        )
        try:
            image_bytes = await asyncio.to_thread(
                render_card_reveal,
                self._renderer,
                card,
                copies,
                output_path,
                mode=mode,
                before_copies=before_copies,
                is_kaika=is_kaika,
                is_cho_kaika=is_cho_kaika,
                character_name=character_name,
            )
            image_base64 = base64.b64encode(image_bytes).decode("ascii")
            sent = await self.ctx.send.image(image_base64, stream_id)
            return sent is True or isinstance(sent, dict) and (sent.get('sent') is True or sent.get('success') is True)
        except Exception as exc:
            self.ctx.logger.error(
                "卡牌揭示图发送失败: %s",
                exc,
                exc_info=True,
            )

            return False

    async def _send_pending_card_reveals(self, stream_id: str, user_id: str) -> None:
        """发送成功后确认单条事件；失败保留队列，后续指令可继续发送。"""
        if self._db is None or self._cards is None:
            return
        if (
            not self._render_ready
            or not self._card_images_ready
            or not self._ui_assets_ready
        ):
            # 当前无法产出揭示图，队列保留到素材接入后继续发送。
            return
        async with self._reveal_lock:
            events = await asyncio.to_thread(self._db.list_pending_card_reveals, user_id)
            for event in events:
                card = self._cards.by_id.get(event['card_id'])
                if card is None:
                    continue
                sent = await self._send_card_reveal(stream_id, card, event['after_copies'],
                    mode='new' if event['before_copies'] <= 0 else 'star_up',
                    before_copies=event['before_copies'],
                    is_kaika=event['is_kaika'],
                    is_cho_kaika=event['is_cho_kaika'])
                if not sent:
                    break
                await asyncio.to_thread(self._db.ack_card_reveal, user_id, event['id'])

    async def _send_item_gain_card(
        self,
        stream_id: str,
        user_id: str,
        items: dict[str, int],
        *,
        title: str = "获得物品",
        subtitle: str = "",
    ) -> bool:
        """有新增物品时发送获得提示图；无新增返回 False。"""
        gained = {
            key: int(amount or 0)
            for key, amount in items.items()
            if int(amount or 0) > 0
        }
        if not gained:
            return False
        if not self._render_ready:
            return False
        totals: dict[str, int] = {}
        if self._growth is not None:
            try:
                snapshot = await asyncio.to_thread(self._growth.snapshot, user_id)
                totals = {
                    str(row["item_id"]): int(row["quantity"])
                    for row in snapshot["player_items"]
                }
            except Exception as exc:
                self.ctx.logger.warning("读取物品结余失败，物品图省略持有量: %s", exc)
        output_path = self.ctx.paths.runtime_dir / f"ongeki_item_{time_ns()}.png"
        try:
            path = await asyncio.to_thread(
                render_item_gain,
                gained,
                output_path,
                title=title,
                subtitle=subtitle,
                totals=totals,
            )
        except Exception as exc:
            self.ctx.logger.warning("物品获得图片渲染失败: %s", exc)
            return False
        if path is None:
            return False
        try:
            image_base64 = base64.b64encode(path.read_bytes()).decode("ascii")
            result = await self.ctx.send.image(image_base64, stream_id)
        except Exception as exc:
            self.ctx.logger.warning("物品获得图片发送失败: %s", exc)
            return False
        return result is True or (
            isinstance(result, dict)
            and (result.get("sent") is True or result.get("success") is True)
        )

    @staticmethod
    def _rare_summary(cards: list[CardInfo]) -> str:
        counts = Counter(rarity_display(card.rarity) for card in cards)
        parts = []
        for label in ("SSR", "SR+", "SR", "R", "N"):
            if counts[label]:
                parts.append(f"{label}×{counts[label]}")
        return " · ".join(parts) if parts else "无"

    def _draw_text_list(
        self,
        cards: list[CardInfo],
        receipt: Any,
    ) -> str:
        """抽卡结果的文字清单；渲染不可用时用它替代结果图。"""
        lines = ["【抽卡结果】"]
        for card, commitment in zip(cards, receipt.commitments, strict=True):
            marks = []
            if commitment.is_new:
                marks.append("NEW")
            if commitment.is_cho_kaika:
                marks.append("超解花")
            elif commitment.is_kaika:
                marks.append("解花")
            if commitment.fragments:
                marks.append(f"碎片+{commitment.fragments}")
            suffix = f"｜{'·'.join(marks)}" if marks else ""
            lines.append(
                f"{rarity_display(card.rarity)} {card.id} "
                f"{self._card_display_name(card, 18)}{suffix}"
            )
        return "\n".join(lines)

    @staticmethod
    def _card_display_name(card: CardInfo, limit: int | None = None) -> str:
        """返回去掉开头【稀有度】标签后的卡名，避免列表里重复显示稀有度。"""
        name = re.sub(r"^【[^】]+】\s*", "", card.name).strip() or card.name
        return OngekiGachaPlugin._ellipsize(name, limit)

    @staticmethod
    def _ellipsize(text: str, limit: int | None = None) -> str:
        """超长文本按字符数截断并追加省略号，避免排版截断不清。"""
        value = str(text or "")
        if limit is None or len(value) <= limit:
            return value
        return value[: max(limit - 1, 0)] + "…"

    @staticmethod
    def _pool_kind_label(kind: str) -> str:
        """把排表里的内部类型转换为用户可读的中文标签。"""
        normalized = str(kind or "").strip().lower()
        return POOL_KIND_LABELS.get(normalized, str(kind or ""))

    # ==================== 命令 ====================

    @Command(
        "ongeki_draw",
        description="音击抽卡模拟器，支持 1/5/11 连",
        pattern=(
            r"^/抽卡(?:\s+(?P<pool>常驻|普通|常规|official-\d+|\d+))?"
            r"(?:\s+(?P<count>11|5|1))?\s*$"
        ),
    )
    async def handle_draw(self, stream_id: str = "", **kwargs: dict[str, Any]) -> tuple[bool, str, bool]:
        """执行抽卡并发送结果图。"""
        user_id = self._user_id(kwargs)
        count = self._parse_count(kwargs)
        regular_draw = self._is_regular_draw(kwargs)
        pool_selector = self._draw_pool_selector(kwargs)
        cost = self._cost(count)
        if cost <= 0:
            text = "数量只能是 1/5/11｜用法 /抽卡 11"
            await self._send_text(stream_id, text)
            return True, text, True

        async with self._lock:
            if (
                self._db is None
                or self._renderer is None
                or self._cards is None
                or (regular_draw and self._regular_pool_instance is None)
            ):
                text = "插件未就绪，请稍后重试"
                await self._send_text(stream_id, text)
                return True, text, True
            self._sync_active_pool(pool_selector)
            draw_pool = self._regular_pool_instance if regular_draw else self._pool
            if draw_pool is None and pool_selector:
                active_names = "、".join(
                    item.pool_id for item in self._active_pool_entries
                )
                if pool_selector.isdigit() and int(pool_selector) <= 11:
                    text = (
                        f"没有编号 {pool_selector} 的卡池（数量只能是 1/5/11）"
                        "\n/卡池 列表 查看可用池"
                    )
                else:
                    text = (
                        f"卡池 {pool_selector} 未启用"
                        f"｜当前 {active_names or '无'}"
                    )
                await self._send_text(stream_id, text)
                return True, text, True
            if draw_pool is None:
                draw_pool = self._regular_pool_instance
            if draw_pool is None:
                text = "当前没有可用卡池"
                await self._send_text(stream_id, text)
                return True, text, True

            original_cost = cost
            try:
                settled = self._db.perform_draw(
                    user_id, draw_pool, count, cost=cost,
                    request_id=request_identity(kwargs, stream_id),
                    tz_offset_hours=self.config.economy.tz_offset_hours,
                )
            except ValueError as exc:
                text = str(exc)
                await self._send_text(stream_id, text)
                return True, text, True
            except Exception as exc:
                self.ctx.logger.exception("抽卡执行失败: %s", exc)
                text = "抽卡失败，已回滚本次消耗；请稍后重试"
                await self._send_text(stream_id, text)
                return True, text, True
            receipt = settled['receipt']
            if not receipt.success:
                text = receipt.error or "抽卡失败"
                await self._send_text(stream_id, text)
                return True, text, True
            half_price_used = settled['half_price_used']
            weekly_5_claimed = settled['weekly_5_claimed']
            cost = settled['cost']
            drawn_cards = [self._cards.by_id[item.card_id] for item in receipt.commitments]

            states = [
                RenderCard(
                    card=card,
                    copies=commitment.copies,
                    is_kaika=commitment.is_kaika,
                    is_cho_kaika=commitment.is_cho_kaika,
                )
                for card, commitment in zip(drawn_cards, receipt.commitments, strict=True)
            ]
            pool_label = (
                draw_pool.pool_id
                if draw_pool.pool_id != "regular"
                else "常驻"
            )
            summary_lines = [
                f"{count} 连｜{pool_label}",
                f"{self._rare_summary(drawn_cards)}"
                f"｜新卡 {sum(1 for item in receipt.commitments if item.is_new)}"
                f"｜碎片 +{sum(item.fragments for item in receipt.commitments)}",
                f"剩余 {receipt.points} 点",
            ]
            if draw_pool.featured_count:
                summary_lines.append(
                    f"UP {draw_pool.featured_count} 张 ×{draw_pool.pickup_multiplier}"
                )
            if receipt.max_select_points:
                if receipt.select_claimed:
                    ceiling_text = "天井已兑换"
                elif receipt.select_ready:
                    ceiling_text = (
                        f"天井已满 {receipt.select_points}/"
                        f"{receipt.max_select_points}，/天井 <卡ID> 兑换"
                    )
                else:
                    ceiling_text = (
                        f"天井 {receipt.select_points}/"
                        f"{receipt.max_select_points}"
                    )
                summary_lines.append(ceiling_text)
            if count == 5:
                summary_lines.append(
                    "5 连保底已用"
                    if weekly_5_claimed
                    else "5 连保底本周已用"
                )
                if half_price_used:
                    summary_lines.append(
                        f"月卡半价 {original_cost}→{cost} 点"
                    )
            summary = "\n".join(summary_lines)
            image_sent = False
            if (
                not self._render_ready
                or not self._card_images_ready
                or not self._ui_assets_ready
            ):
                # 未接入字体或卡面时不画占位图，直接发文字清单。
                self.ctx.logger.info("未接入渲染素材，抽卡结果改为文字清单")
            else:
                output_path = self.ctx.paths.runtime_dir / f"ongeki_draw_{count}_{time_ns()}.png"
                try:
                    image_bytes = self._renderer.render(states, output_path)
                    image_base64 = base64.b64encode(image_bytes).decode("ascii")
                    await self.ctx.send.image(image_base64, stream_id)
                    image_sent = True
                except Exception as exc:
                    self.ctx.logger.error("抽卡图片发送失败: %s", exc, exc_info=True)
            if not image_sent:
                summary = "\n".join(
                    [*summary_lines, self._draw_text_list(drawn_cards, receipt)]
                )

            for card, commitment in zip(drawn_cards, receipt.commitments, strict=True):
                if card.rarity == "SSR":
                    await self._send_card_reveal(
                        stream_id,
                        card,
                        commitment.copies,
                        mode="new" if commitment.is_new else "star_up",
                        before_copies=0 if commitment.is_new else max(0, commitment.copies - 1),
                        is_kaika=commitment.is_kaika,
                        is_cho_kaika=commitment.is_cho_kaika,
                    )

        await self._send_text(stream_id, summary)
        await self._send_pending_card_reveals(stream_id, user_id)
        return True, summary, True

    @Command("ongeki_checkin", description="每日签到领取基础、连续签到、卡池周期和月卡奖励", pattern=r"^/签到\s*$")
    async def handle_checkin(self, stream_id: str = "", **kwargs: dict[str, Any]) -> tuple[bool, str, bool]:
        """执行每日签到。"""
        user_id = self._user_id(kwargs)
        config = self.config.economy
        savings_bonus = 0
        final_points = 0
        async with self._lock:
            if self._db is None:
                text = "插件未就绪，请稍后重试"
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
                    reset_days=config.savings_bonus_reset_days,
                    tz_offset_hours=config.tz_offset_hours,
                )
        if receipt.success:
            parts = [
                f"签到 +{receipt.reward} 点｜连续第 {receipt.streak_days} 天",
            ]
            if receipt.streak_extra:
                parts[0] += f"｜连续 +{receipt.streak_extra}"
            if receipt.weekly_reward:
                parts[0] += f"｜7 天 +{receipt.weekly_reward}"
            if receipt.cycle_reward:
                parts[0] += f"｜周期 +{receipt.cycle_reward}"
            if receipt.monthly_reward:
                parts[0] += f"｜月卡 +{receipt.monthly_reward}"
            if savings_bonus:
                parts[0] += f"｜囤点 +{savings_bonus}"
            parts.append(f"当前 {final_points or receipt.points} 点")
            if receipt.non_gacha_card_id:
                bonus_card = (
                    self._cards.by_id.get(receipt.non_gacha_card_id)
                    if self._cards is not None
                    else None
                )
                if bonus_card is not None:
                    parts.append(f"彩蛋卡 {self._card_display_name(bonus_card)}")
                    await self._send_checkin_bonus_card(
                        stream_id,
                        bonus_card,
                        receipt.non_gacha_copies,
                        receipt.non_gacha_is_kaika,
                        receipt.non_gacha_is_cho_kaika,
                        receipt.non_gacha_is_new,
                    )
            if self.config.growth.enabled:
                gained = []
                gained_items = {
                    "gift_small": receipt.small_gifts,
                    "gift_medium": receipt.medium_gifts,
                    "gift_large": receipt.large_gifts,
                    "flower_fragment": receipt.growth_fragments,
                }
                for label, amount in (("小礼物", receipt.small_gifts),
                                      ("中礼物", receipt.medium_gifts),
                                      ("大礼物", receipt.large_gifts)):
                    if amount:
                        gained.append(f"{label} ×{amount}")
                if receipt.growth_fragments:
                    gained.append(f"花之碎片 +{receipt.growth_fragments}")
                if gained:
                    parts.append("养成 " + "、".join(gained))
                if any(gained_items.values()):
                    await self._send_item_gain_card(
                        stream_id,
                        user_id,
                        gained_items,
                        title="签到获得物品",
                        subtitle=f"连续签到第 {receipt.streak_days} 天",
                    )
            text = "\n".join(parts)
        else:
            text = receipt.error or "签到失败"
        await self._send_text(stream_id, text)
        await self._send_pending_card_reveals(stream_id, user_id)
        return True, text, True

    @Command("ongeki_points", description="查看点数、花之碎片、连续签到和月卡状态", pattern=r"^/点数\s*$")
    async def handle_points(self, stream_id: str = "", **kwargs: dict[str, Any]) -> tuple[bool, str, bool]:
        """查看用户点数。"""
        user_id = self._user_id(kwargs)
        async with self._lock:
            if self._db is None:
                text = "插件未就绪，请稍后重试"
                await self._send_text(stream_id, text)
                return True, text, True
            savings_bonus, _ = self._db.grant_savings_bonus(
                user_id,
                thresholds=self._savings_thresholds(),
                bonuses=self._savings_bonuses(),
                reset_days=self.config.economy.savings_bonus_reset_days,
                tz_offset_hours=self.config.economy.tz_offset_hours,
            )
            player = self._db.get_player(user_id)
            fragments = 0
            bloom_tickets = 0
            if self._growth is not None:
                growth_snapshot = await asyncio.to_thread(
                    self._growth.snapshot,
                    user_id,
                )
                fragments = next(
                    (
                        int(row["quantity"])
                        for row in growth_snapshot["player_items"]
                        if row["item_id"] == "flower_fragment"
                    ),
                    0,
                )
                bloom_tickets = next(
                    (
                        int(row["quantity"])
                        for row in growth_snapshot["player_items"]
                        if row["item_id"] == "bloom_ticket"
                    ),
                    0,
                )
            weekly_5_available = self._db.weekly_5_guarantee_available(
                user_id,
                tz_offset_hours=self.config.economy.tz_offset_hours,
            )
            remaining_days = self._monthly_card_remaining_days(
                player.monthly_card_expires_at,
                self.config.economy.tz_offset_hours,
            )
            if remaining_days > 0:
                monthly_text = f"剩余 {remaining_days} 天"
            elif player.monthly_card_purchase_count > 0:
                monthly_text = "已过期"
            else:
                monthly_text = "未购买"
            savings_reset_text = (
                f"{self.config.economy.savings_bonus_reset_days} 天后重置"
            )
            if player.savings_bonus_start_date:
                try:
                    start_day = date.fromisoformat(player.savings_bonus_start_date)
                    current = self._today(self.config)
                    next_reset = start_day + timedelta(
                        days=self.config.economy.savings_bonus_reset_days
                    )
                    remaining_reset = max((next_reset - current).days, 0)
                    savings_reset_text = f"{remaining_reset} 天后重置"
                except ValueError:
                    pass
        lines = [
            f"点数 {player.points}｜花之碎片 {fragments}｜解花券 {bloom_tickets}",
            f"签到 {player.total_checkins} 次｜抽卡 {player.total_pulls} 次"
            f"｜连续 {player.streak_days} 天",
            f"月卡 {monthly_text}｜半价 5 连 {player.half_price_5_pull_count} 次",
            f"囤点 {player.savings_bonus_level}/3（{savings_reset_text}）"
            f"｜5 连保底{'可用' if weekly_5_available else '已用'}",
        ]
        if savings_bonus:
            lines.append(f"本次入账囤点 +{savings_bonus} 点")
        text = "\n".join(lines)
        await self._send_text(stream_id, text)
        return True, text, True

    @Command(
        "ongeki_monthly_card",
        description="购买、续费或查看月卡",
        pattern=r"^/月卡(?:\s+(?P<action>购买|续费|查看))?\s*$",
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
                text = "插件未就绪，请稍后重试"
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
                        f"月卡{verb}成功｜-{config.price} 点\n"
                        f"到期 {receipt.expires_at}（{receipt.remaining_days} 天）"
                        f"｜每日 +{config.daily_bonus}"
                        f"｜半价 5 连 +{config.half_price_5_pull_count}\n"
                        f"剩余 {receipt.points} 点"
                    )
                else:
                    text = receipt.error or "月卡购买失败"
            else:
                if remaining_days > 0:
                    status = f"有效 剩余 {remaining_days} 天"
                elif player.monthly_card_purchase_count > 0:
                    status = "已过期"
                else:
                    status = "未购买"
                text = "\n".join(
                    [
                        f"月卡 {status}｜{config.price} 点 / {config.duration_days} 天",
                        f"每日 +{config.daily_bonus}"
                        f"｜半价 5 连 {player.half_price_5_pull_count} 次"
                        f"｜续费需剩余 ≤{config.renew_max_remaining_days} 天",
                        f"点数 {player.points}｜/月卡 购买 或 /月卡 续费",
                    ]
                )
        await self._send_text(stream_id, text)
        return True, text, True

    @Command(
        "ongeki_grant_points",
        description="管理员向指定 QQ 用户发放点数",
        pattern=r"^/奖励\s+(?:(?P<target_at>@\S+)|(?P<target_id>\d+))\s+(?P<amount>\d+)(?:\s+(?P<note>.+))?\s*$",
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
            text = (
                "没识别到目标｜用法 /奖励 @用户 <点数> 或 /奖励 <QQ号> <点数>"
            )
            await self._send_text(stream_id, text)
            return True, text, True
        target_id, amount, note = parsed
        if not self._is_admin(user_id):
            text = "仅管理员可发放点数"
            await self._send_text(stream_id, text)
            return True, text, True

        async with self._lock:
            if self._db is None:
                text = "插件未就绪，请稍后重试"
                await self._send_text(stream_id, text)
                return True, text, True
            receipt = self._db.grant_points(
                user_id,
                target_id,
                amount,
                note=note,
            )
        if receipt.success:
            text = f"已向 {target_id} 发放 {amount} 点｜当前 {receipt.points} 点"
            if note:
                text += f"｜{note}"
        else:
            text = receipt.error or "发放点数失败"
        await self._send_text(stream_id, text)
        return True, text, True

    @Command(
        "ongeki_card_image",
        description="查看已拥有的卡牌高清大图",
        pattern=r"^/卡图\s+(?P<card_id>\d+)\s*$",
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
            if self._db is None or self._cards is None or self._cards_dir is None:
                text = "插件未就绪，请稍后重试"
                await self._send_text(stream_id, text)
                return True, text, True

            inventory = self._db.get_inventory(user_id)
            owned = {entry.card_id for entry in inventory}
            if card_id not in owned:
                text = f"未拥有卡 ID {card_id}（/卡册 角色 1 查卡ID）"
                await self._send_text(stream_id, text)
                return True, text, True

            card = self._cards.by_id.get(card_id)
            if card is None:
                text = f"卡牌 ID {card_id} 不存在"
                await self._send_text(stream_id, text)
                return True, text, True

            card_path = self._card_image_path(card)
            if not card_path.is_file():
                text = (
                    f"卡牌 ID {card_id} 卡面未接入，暂不发图｜"
                    "接入方法见 ASSETS.md"
                )
                self.ctx.logger.info("%s", text)
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

        text = f"卡图 {card_id}：{self._card_display_name(card, 24)}"
        await self._send_text(stream_id, text)
        return True, text, True

    def _card_image_path(self, card: CardInfo) -> Path:
        """返回当前运行实例实际使用的卡面文件路径。"""
        if self._cards_dir is None:
            raise RuntimeError("卡牌数据目录尚未初始化")
        return self._cards_dir / card.image_file

    @Command(
        "ongeki_inventory",
        description="按角色查看卡册与卡ID",
        pattern=r"^/卡册(?:\s+(?P<catalog_args>.+))?\s*$",
    )
    async def handle_inventory(
        self,
        stream_id: str = "",
        matched_groups: dict | None = None,
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        """按角色分页查看卡册；页码由用户指定，单次只发一页。"""
        user_id = self._user_id(kwargs)
        raw = str((matched_groups or {}).get("catalog_args") or "").strip()
        parts = raw.split()
        page = 1
        if parts and parts[-1].isdigit():
            page = int(parts[-1])
            parts = parts[:-1]
        query = "".join(parts)
        async with self._lock:
            if (
                self._db is None
                or self._cards is None
                or self._growth is None
                or self._renderer is None
            ):
                text = "插件未就绪，请稍后重试"
                await self._send_text(stream_id, text)
                return True, text, True
            inventory = self._db.get_inventory(user_id)
            snapshot = await asyncio.to_thread(self._growth.snapshot, user_id)
            groups = catalog_buckets(self._cards)
            owned = {entry.card_id: entry.copies for entry in inventory}
            profile = (
                snapshot["player_growth_profile"][0]
                if snapshot["player_growth_profile"]
                else {}
            )
            partner = profile.get("partner_character_id")
            output = self.ctx.paths.runtime_dir / f"ongeki_catalog_{time_ns()}.png"
            if not query:
                try:
                    if not self._render_ready:
                        raise RuntimeError("未接入渲染素材")
                    path = await asyncio.to_thread(
                        render_catalog_index,
                        self._growth.catalog,
                        groups,
                        owned,
                        output,
                        partner=partner,
                    )
                    text = (
                        "卡册总览已发送：发送 /卡册 <角色姓名> <页码> 查看指定角色；"
                        "非主角色统一归入“其他”。"
                    )
                except Exception as exc:
                    self.ctx.logger.warning("卡册总览渲染失败，回退文字: %s", exc)
                    text = "\n".join(
                        catalog_index_text(
                            self._growth.catalog, groups, owned, partner=partner
                        )
                    )
                    await self._send_text(stream_id, text, title="音击抽卡模拟器 · 卡册")
                    return True, text, True
            else:
                try:
                    key = (
                        OTHER_KEY
                        if query in {"其他", "其它", "OTHER", "other"}
                        else resolve_character_name(self._growth.catalog, query)
                    )
                except ValueError as exc:
                    text = f"{exc}\n用法：/卡册 <角色姓名> <页码>，角色名见 /卡册"
                    await self._send_text(stream_id, text)
                    return True, text, True
                rows = groups[key]
                if not rows:
                    text = "该分类暂无卡牌数据"
                    await self._send_text(stream_id, text)
                    return True, text, True
                if key == OTHER_KEY:
                    name = OTHER_LABEL
                    query_name = OTHER_LABEL
                    character = None
                else:
                    name = self._growth.catalog.characters[key]["name"]
                    query_name = name
                    character = key
                try:
                    if not self._render_ready:
                        raise RuntimeError("未接入渲染素材")
                    path, page, pages = await asyncio.to_thread(
                        render_catalog_page,
                        self._growth.catalog,
                        rows,
                        owned,
                        output,
                        name=name,
                        query=query_name,
                        page=page,
                        character=character,
                        partner=partner,
                    )
                    text = (
                        f"卡册 · {name} 第 {page}/{pages} 页已发送；"
                        f"发送 /卡册 {query_name} <页码> 翻页"
                    )
                except Exception as exc:
                    self.ctx.logger.warning("卡册分页渲染失败，回退文字: %s", exc)
                    text = "\n".join(
                        catalog_page_text(name, rows, owned, page, query_name)
                    )
                    await self._send_text(stream_id, text, title="音击抽卡模拟器 · 卡册")
                    return True, text, True
        try:
            image_base64 = base64.b64encode(path.read_bytes()).decode("ascii")
            await self.ctx.send.image(image_base64, stream_id)
        except Exception as exc:
            self.ctx.logger.warning("卡册图片发送失败: %s", exc)
            await self._send_text(stream_id, text)
        return True, text, True

    @Command(
        "ongeki_pool",
        description="查看当前启用卡池、UP 卡与轮替信息",
        pattern=r"^/卡池(?:\s+(?P<action>列表|下一期))?\s*$",
    )
    async def handle_pool(
        self,
        stream_id: str = "",
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        """显示当前卡池和历史轮替信息。"""
        user_id = self._user_id(kwargs)
        action = self._action_from_kwargs(kwargs, ("列表", "下一期"))
        del kwargs
        image_urls: list[str] = []
        async with self._lock:
            if self._schedule is None or self._cards is None:
                text = "插件未就绪，请稍后重试"
                await self._send_text(stream_id, text)
                return True, text, True
            self._sync_active_pool()
            schedule = self._schedule
            mode = str(self.config.pool.rotation_mode or "cycle").strip().lower()
            today = self._today(self.config)
            lines: list[str] = []
            shown_pool: PoolEntry | None = None
            index: int | None = None

            def up_ssr_lines(entry: PoolEntry) -> list[str]:
                """Return the UP SSR character lines for one pool."""
                result: list[str] = []
                for pool_card in entry.up_ssr_cards():
                    card = self._cards.by_id.get(pool_card.card_id)
                    if card is not None:
                        result.append(
                            f"{self._card_display_name(card, 38)}"
                            f"（ID {card.id}）"
                        )
                return result

            def append_pool_details(
                entry: PoolEntry,
                *,
                detailed: bool,
                include_meta: bool = True,
            ) -> None:
                if detailed:
                    # /卡池 列表只输出角色信息，不附带类型/时间与卡池公告图
                    up_ssr = entry.up_ssr_cards()
                    lines.append(f"UP SSR：{len(up_ssr)} 张")
                    lines.extend(up_ssr_lines(entry))
                    return
                if include_meta:
                    if mode == "official" and entry.start_date and entry.end_date:
                        period = (
                            f"{entry.start_date:%m-%d} ～ "
                            f"{entry.end_date:%m-%d}"
                        )
                    else:
                        period = f"{entry.start_date} ～ {entry.end_date}"
                    lines.append(
                        f"类型：{self._pool_kind_label(entry.kind)}｜"
                        f"时间：{period}"
                    )
                if entry.image_url:
                    image_urls.append(entry.image_url)
                up_ssr = entry.up_ssr_cards()
                lines.append(
                    f"UP SSR：{len(up_ssr)} 张｜"
                    f"UP 卡：{entry.featured_count} 张｜"
                    f"天井选择：{entry.select_count} 张"
                )
                lines.extend(up_ssr_lines(entry)[:6])
                if len(up_ssr) > 6:
                    lines.append("完整列表：/卡池 列表")

            if mode == "official":
                active_entries = self._active_pool_entries
                if not active_entries:
                    lines = [
                        "本期卡池：常驻池（当前版本已有全部 R/SR/SSR）",
                        f"状态：今天 {today:%m-%d} 没有官方活动池，使用常驻池",
                    ]
                else:
                    if action == "列表":
                        lines.append(
                            f"今天 {today:%m-%d} 启用 "
                            f"{len(active_entries)} 个官方卡池的 UP 角色："
                        )
                    else:
                        lines.append(
                            f"今天 {today:%m-%d} 同时启用 "
                            f"{len(active_entries)} 个官方卡池："
                        )
                    for order, entry in enumerate(active_entries, 1):
                        lines.append(
                            f"{order}. {entry.pool_id}｜"
                            f"{self._ellipsize(entry.name, 60)}"
                        )
                        append_pool_details(
                            entry,
                            detailed=action == "列表",
                        )
                    default_entry = max(
                        active_entries,
                        key=lambda item: item.start_date or date.min,
                    )
                    if action != "列表":
                        lines.append(
                            f"默认抽卡：{default_entry.pool_id}"
                            f"｜/抽卡 <池ID> <1|5|11> 可指定其他启用池"
                        )
                    shown_pool = default_entry
            else:
                index = self._cycle_index(schedule)
                if index is None:
                    lines = ["卡池排表为空"]
                else:
                    interval = max(
                        int(self.config.pool.rotation_interval_days or 1),
                        1,
                    )
                    epoch = self._get_rotation_epoch()
                    elapsed = max((today - epoch).days, 0)
                    cycle_number = elapsed // interval
                    cycle_start = epoch + timedelta(days=cycle_number * interval)
                    cycle_end = cycle_start + timedelta(days=interval - 1)
                    next_start = cycle_end + timedelta(days=1)
                    shown_pool = schedule.entries[index]
                    if action == "列表":
                        lines = [
                            "本期卡池："
                            f"{self._ellipsize(shown_pool.name, 60)}",
                            f"UP SSR：{len(shown_pool.up_ssr_cards())} 张",
                        ]
                        lines.extend(up_ssr_lines(shown_pool))
                    else:
                        original = (
                            f"｜原公告日期（参考）：{shown_pool.start_date} ～ "
                            f"{shown_pool.end_date}"
                            if shown_pool.start_date and shown_pool.end_date
                            else ""
                        )
                        lines = [
                            f"本期卡池："
                            f"{self._ellipsize(shown_pool.name, 60)}",
                            f"类型：{self._pool_kind_label(shown_pool.kind)}",
                            f"模拟档期：{cycle_start.isoformat()} ～ "
                            f"{cycle_end.isoformat()}"
                            f"（第 {cycle_number + 1} 轮｜每 {interval} 天）",
                            f"排表位置：第 {index + 1}/"
                            f"{len(schedule.entries)} 期{original}",
                            f"下次轮替：{next_start.isoformat()}",
                        ]
                        append_pool_details(
                            shown_pool,
                            detailed=False,
                            include_meta=False,
                        )
                        lines.append(
                            "抽卡：/抽卡 <1|5|11>（本期池）"
                            "｜/抽卡 常驻 <1|5|11>（常驻池）"
                        )

            if action != "列表" and self._db is not None:
                ceiling_entries = (
                    [
                        entry
                        for entry in active_entries
                        if entry.select_points
                    ]
                    if mode == "official" and active_entries
                    else ([shown_pool] if shown_pool is not None else [])
                )
                for pool_entry in ceiling_entries:
                    state = self._db.get_pool_select_state(
                        user_id,
                        pool_entry.pool_id,
                    )
                    max_points = max(
                        int(pool_entry.select_points or 0),
                        state.max_select_points,
                    )
                    if max_points <= 0:
                        continue
                    if state.is_claimed:
                        ceiling_status = "本池已兑换"
                    elif state.is_ready:
                        exchange_hint = (
                            f"/天井池 {pool_entry.pool_id} <卡ID>"
                            if len(ceiling_entries) > 1
                            else "/天井 <卡ID>"
                        )
                        ceiling_status = (
                            f"已满 {state.select_points}/{max_points}，"
                            f"可 {exchange_hint}"
                        )
                    else:
                        ceiling_status = (
                            f"{state.select_points}/{max_points}"
                        )
                    label = (
                        f"你的天井 {pool_entry.pool_id}"
                        if len(ceiling_entries) > 1
                        else "你的天井"
                    )
                    lines.append(f"{label}：{ceiling_status}")
            if action != "列表" and self._regular_pool is not None:
                regular_pool_name = (
                    self._ellipsize(self._regular_pool_instance.pool_name, 60)
                    if self._regular_pool_instance is not None
                    else self._ellipsize(self._regular_pool.name, 60)
                )
                lines.append(
                    f"常驻池：{regular_pool_name}"
                    f"（{len(self._regular_pool.cards)} 张，可 /抽卡 常驻 使用）"
                )
            if action == "下一期" and index is not None and mode != "official":
                next_pool = schedule.entries[(index + 1) % len(schedule.entries)]
                next_end = next_start + timedelta(days=interval - 1)
                lines.append(
                    f"下一期：{self._ellipsize(next_pool.name, 40)}"
                    f"｜模拟档期 {next_start.isoformat()} ～ "
                    f"{next_end.isoformat()}"
                )
            if action != "列表":
                lines.append(
                    "排表来源：官方历史卡池公告；模拟档期由本插件按"
                    "轮替周期复刻，不代表官方现行日程"
                )
            text = "\n".join(lines)
        await self._send_text(stream_id, text, title="音击抽卡模拟器 · 卡池")
        if action != "列表":
            for image_url in dict.fromkeys(image_urls):
                await self._send_official_pool_image(stream_id, image_url)
        return True, text, True

    @Command(
        "ongeki_ceiling",
        description="查看或兑换当前卡池天井选择卡",
        pattern=(
            r"^/天井"
            r"(?:\s+(?P<action>查看))?"
            r"(?:\s+(?P<card_id>\d+))?\s*$"
        ),
    )
    async def handle_ceiling(
        self,
        stream_id: str = "",
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        """查看天井状态，或在天井满后兑换选择卡。"""
        user_id = self._user_id(kwargs)
        action = self._action_from_kwargs(kwargs, ("列表", "查看"))
        groups = kwargs.get("matched_groups")
        pool_selector = (
            str(groups.get("pool_selector") or "").strip()
            if isinstance(groups, dict)
            else ""
        )
        raw_card_id = (
            str(groups.get("card_id") or "").strip()
            if isinstance(groups, dict)
            else ""
        )
        card_id = int(raw_card_id) if raw_card_id.isdigit() else self._parse_card_id(kwargs)
        del kwargs

        async with self._lock:
            if self._db is None or self._cards is None:
                text = "插件未就绪，请稍后重试"
                await self._send_text(stream_id, text)
                return True, text, True
            self._sync_active_pool()
            active_entries = self._active_pool_entries
            if not active_entries:
                text = "当前没有活动池"
                await self._send_text(stream_id, text)
                return True, text, True

            def entry_state(entry: PoolEntry) -> tuple[int, object, list[object], set[int]]:
                max_points = entry.select_points or 0
                state = self._db.get_pool_select_state(user_id, entry.pool_id)
                selectable = [
                    pool_card
                    for pool_card in entry.cards.values()
                    if pool_card.is_select and pool_card.card_id not in STARTER_CARD_IDS
                ]
                return (
                    max_points,
                    state,
                    selectable,
                    {item.card_id for item in selectable},
                )

            if action == "列表":
                lines: list[str] = [f"当前启用 {len(active_entries)} 个卡池："]
                for entry in active_entries:
                    max_points, state, selectable, _ = entry_state(entry)
                    lines.append(
                        f"【{entry.pool_id}】{self._ellipsize(entry.name, 24)}"
                    )
                    if max_points <= 0:
                        lines.append("无天井")
                        continue
                    lines.append(
                        f"天井上限：{max_points}｜"
                        f"进度：{state.select_points}/{max_points}｜"
                        f"可选卡：{len(selectable)} 张"
                    )
                    for pool_card in selectable:
                        card = self._cards.by_id.get(pool_card.card_id)
                        if card is None:
                            continue
                        lines.append(
                            f"{rarity_display(card.rarity)} "
                            f"{self._card_display_name(card, 38)}"
                            f"（ID {card.id}）"
                        )
                lines.append(
                    "/天井 <卡ID> 兑换｜同卡多池用 /天井池 <池ID> <卡ID>"
                )
                text = "\n".join(lines)
            elif card_id is None:
                lines = [f"当前启用 {len(active_entries)} 个卡池："]
                for entry in active_entries:
                    max_points, state, selectable, _ = entry_state(entry)
                    if max_points <= 0:
                        continue
                    if state.is_claimed:
                        status = "已兑换"
                    elif state.is_ready:
                        status = (
                            f"已满 {state.select_points}/{max_points}"
                        )
                    else:
                        status = (
                            f"{state.select_points}/{max_points}"
                        )
                    lines.append(
                        f"{entry.pool_id}｜{self._ellipsize(entry.name, 20)}｜"
                        f"{status}｜可选 {len(selectable)} 张"
                    )
                if len(lines) == 1:
                    text = "当前活动池没有天井（/卡池 查看）"
                else:
                    lines.append(
                        "兑换：/天井 <卡ID>；同一张卡属于多个池时用 "
                        "/天井池 <池ID> <卡ID> 指定"
                    )
                    text = "\n".join(lines)
            else:
                def selector_matches(pool_id: str, selector: str) -> bool:
                    key = str(pool_id or "").lower()
                    wanted = str(selector or "").strip().lower()
                    return bool(wanted) and (
                        key == wanted or key.endswith("-" + wanted)
                    )

                def exchange_text(pool: PoolEntry, max_points: int) -> str:
                    card = self._cards.by_id.get(card_id)
                    if card is None:
                        return f"卡牌 ID {card_id} 不存在"
                    receipt = self._db.claim_select_card(
                        user_id,
                        pool.pool_id,
                        card_id,
                        card.rarity,
                        max_select_points=max_points,
                    )
                    if not receipt.success:
                        return receipt.error or "天井兑换失败"
                    verb = "获得" if receipt.is_new else "重复获得"
                    return (
                        f"天井兑换成功（{pool.pool_id}）！{verb} "
                        f"{self._card_display_name(card, 40)}"
                        f"（ID {card.id}）\n"
                        f"当前持有：{receipt.copies} 张\n花之碎片 +{receipt.fragments}\n"
                        f"继续：/养成 {card.id}｜查看天井：/天井"
                    )

                matches: list[
                    tuple[PoolEntry, int, object, list[object]]
                ] = []
                for entry in active_entries:
                    max_points, state, selectable, select_ids = entry_state(
                        entry
                    )
                    if card_id in select_ids and max_points > 0:
                        matches.append(
                            (entry, max_points, state, selectable)
                        )

                if pool_selector:
                    selected_entries = [
                        entry
                        for entry in active_entries
                        if selector_matches(entry.pool_id, pool_selector)
                    ]
                    if not selected_entries:
                        active_names = "、".join(
                            entry.pool_id for entry in active_entries
                        )
                        text = (
                            f"卡池 {pool_selector} 未启用"
                            f"｜当前 {active_names or '无'}"
                        )
                    else:
                        target_pool = selected_entries[0]
                        matched = next(
                            (
                                item
                                for item in matches
                                if item[0].pool_id == target_pool.pool_id
                            ),
                            None,
                        )
                        if matched is None:
                            text = (
                                f"卡牌 ID {card_id} 不在卡池 "
                                f"{target_pool.pool_id} 可选列表"
                                "（/天井列表 查看）"
                            )
                        else:
                            pool, max_points, state, _ = matched
                            if state.is_claimed:
                                text = f"{pool.pool_id} 已经兑换过天井卡"
                            elif not state.is_ready:
                                text = (
                                    f"{pool.pool_id} 天井 {state.select_points}"
                                    f"/{max_points}（/天井列表 查看可选卡）"
                                )
                            else:
                                text = exchange_text(pool, max_points)
                elif not matches:
                    text = (
                        f"卡 ID {card_id} 不在当前卡池（/天井列表 查看）"
                    )
                else:
                    available = [
                        item for item in matches if not item[2].is_claimed
                    ]
                    claimed = [
                        item for item in matches if item[2].is_claimed
                    ]
                    if not available:
                        names = "、".join(
                            item[0].pool_id for item in claimed
                        )
                        text = (
                            f"卡牌 ID {card_id} 所属的卡池都已兑换过"
                            f"天井卡：{names}"
                        )
                    elif len(available) > 1:
                        names = "、".join(
                            item[0].pool_id for item in available
                        )
                        text = (
                            f"卡牌 ID {card_id} 同时属于多个卡池："
                            f"{names}；可使用 /天井池 <池ID> <卡ID> "
                            "指定要兑换的池"
                        )
                        if claimed:
                            text += "\n已兑换：" + "、".join(
                                item[0].pool_id for item in claimed
                            )
                    else:
                        pool, max_points, state, _ = available[0]
                        if not state.is_ready:
                            text = (
                                f"{pool.pool_id} 天井 {state.select_points}"
                                f"/{max_points}（/天井列表 查看可选卡）"
                            )
                        else:
                            text = exchange_text(pool, max_points)
        await self._send_text(stream_id, text, title="音击抽卡模拟器 · 天井选择")
        return True, text, True

    @Command(
        "ongeki_ceiling_list",
        description="查看全部启用池的天井选择卡列表",
        pattern=r"^/天井列表\s*$",
    )
    async def handle_ceiling_list(
        self,
        stream_id: str = "",
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        """独立入口：查看全部启用池可选天井卡的 ID 与角色。"""
        kwargs["matched_groups"] = {"action": "列表"}
        kwargs.pop("stream_id", None)
        return await self.handle_ceiling(stream_id, **kwargs)

    @Command(
        "ongeki_ceiling_pool",
        description="指定启用卡池兑换天井选择卡",
        pattern=(
            r"^/天井池\s+"
            r"(?P<pool_selector>official-\d+|\d+)\s+"
            r"(?P<card_id>\d+)\s*$"
        ),
    )
    async def handle_ceiling_pool(
        self,
        stream_id: str = "",
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        return await self.handle_ceiling(stream_id, **kwargs)

    @Command("ongeki_odds", description="查看抽卡概率与保底规则", pattern=r"^/概率\s*$")
    async def handle_odds(self, stream_id: str = "", **kwargs: dict[str, Any]) -> tuple[bool, str, bool]:
        """显示当前模拟权重。"""
        del kwargs
        config = self.config
        if self._schedule is None or self._cards is None:
            text = "插件未就绪，请稍后重试"
            await self._send_text(stream_id, text)
            return True, text, True
        self._sync_active_pool()
        lines = ["音击抽卡模拟器 · 非官方概率"]
        if self._active_pool_entries:
            mode = str(
                config.pool.rotation_mode or "cycle"
            ).strip().lower()
            scope_label = "官方" if mode == "official" else ""
            lines.append(
                f"当前启用 {len(self._active_pool_entries)} 个"
                f"{scope_label}卡池："
            )
            for entry in self._active_pool_entries:
                lines.append(
                    f"{entry.pool_id}｜{self._ellipsize(entry.name, 50)}｜"
                    f"{self._pool_kind_label(entry.kind)}｜"
                    f"UP 卡：{sum(1 for card in entry.cards.values() if card.is_pickup)} 张｜"
                    f"天井选择：{entry.select_count} 张"
                )
            default_entry = max(
                self._active_pool_entries,
                key=lambda item: item.start_date or date.min,
            )
            lines.append(
                f"默认抽卡：{default_entry.pool_id}；"
                f"/抽卡 <池ID> <1|5|11> 可指定其他启用池"
            )
        else:
            lines.append("当前没有官方活动池，默认使用常驻池")
        weights_pool = self._pool or self._regular_pool_instance
        if weights_pool is None:
            await self._send_text(stream_id, "当前没有可用卡池")
            return True, "当前没有可用卡池", True
        lines.append(
            f"常驻池：{self._ellipsize(self._regular_pool_instance.pool_name, 60)}"
            "（可 /抽卡 常驻 使用）"
            if self._regular_pool_instance is not None
            else "常驻池：未配置"
        )
        if weights_pool.select_count:
            lines.append("天井选择 ID 与角色：/天井列表")
        weights = weights_pool.rarity_weights
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
            "5 连保底重置：每周四 00:00（"
            + self._timezone_label(config.economy.tz_offset_hours)
            + "）"
        )
        text = "\n".join(lines)
        await self._send_text(stream_id, text, title="音击抽卡模拟器 · 概率")
        return True, text, True

    @Command(
        "ongeki_rules",
        description="查看详细规则说明",
        pattern=r"^/规则\s*$",
    )
    async def handle_rules(
        self,
        stream_id: str = "",
        **kwargs: dict[str, Any],
    ) -> tuple[bool, str, bool]:
        """以图片卡片形式发送详细规则，失败时回退纯文本。"""
        del kwargs
        await self._send_text(
            stream_id,
            "\n".join(self._rules_text()),
            title="音击抽卡模拟器 · 规则",
        )
        return True, "详细规则已发送", True

    @Command("ongeki_help", description="显示命令与玩法帮助", pattern=r"^/帮助\s*$")
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
