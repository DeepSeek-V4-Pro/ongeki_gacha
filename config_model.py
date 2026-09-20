"""插件配置模型。"""

from __future__ import annotations

from typing import Any

from maibot_sdk import Field, PluginConfigBase
from pydantic import model_validator


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="1.3.0", description="配置版本")


class AssetsConfig(PluginConfigBase):
    """本地素材路径配置。"""

    __ui_label__ = "素材路径"
    __ui_icon__ = "folder-open"
    __ui_order__ = 1

    cards_dir: str = Field(
        default="assets/card_data",
        description="默认读取插件 assets/card_data；可手动填写绝对路径",
    )
    card_info_json: str = Field(
        default="assets/card_data/card_info_merged.json",
        description="默认读取插件 assets/card_data；可手动填写绝对路径",
    )


class PoolConfig(PluginConfigBase):
    """模拟器概率权重与卡池轮替。"""

    __ui_label__ = "概率权重"
    __ui_icon__ = "dices"
    __ui_order__ = 2

    weight_n: int = Field(default=0, ge=0, description="N 权重（正式普通池不使用）")
    weight_r: int = Field(default=77, ge=0, description="R 权重（参照 Artemis 普通池）")
    weight_sr: int = Field(default=20, ge=0, description="SR 权重（参照 Artemis 普通池）")
    weight_sr_plus: int = Field(default=0, ge=0, description="SR+ 权重（正式普通池不使用）")
    weight_ssr: int = Field(default=3, ge=0, description="SSR 权重")
    schedule_json: str = Field(
        default="assets/card_data/gacha_pools.json",
        description="官方卡池排表 JSON；可填写绝对路径",
    )
    rotation_mode: str = Field(
        default="cycle",
        description="official=按几月几日复现历史官方池（忽略年份）；cycle=按间隔循环历史官方池",
    )
    rotation_interval_days: int = Field(
        default=15,
        ge=1,
        description="cycle 模式下每个池的持续天数",
    )
    rotation_epoch: str = Field(
        default="",
        description="cycle 轮替起点 YYYY-MM-DD；留空时使用数据库首次记录且重启不重置",
    )
    pickup_multiplier: int = Field(
        default=10,
        ge=1,
        description="UP 卡相对普通卡的权重倍率",
    )
    strict_pool_cards: bool = Field(
        default=True,
        description="按池子候选列表抽取；常驻池使用 regular_pool，活动池使用排表 cards",
    )

    @model_validator(mode="after")
    def _validate_mode(self) -> "PoolConfig":
        if str(self.rotation_mode or "").strip().lower() not in {"cycle", "official"}:
            raise ValueError("rotation_mode 只能是 cycle 或 official")
        return self


class EconomyConfig(PluginConfigBase):
    """点数、签到与消耗配置。"""

    __ui_label__ = "点数"
    __ui_icon__ = "coins"
    __ui_order__ = 3

    cost_1: int = Field(default=50, ge=0, description="1 连消耗点数")
    cost_5: int = Field(default=250, ge=0, description="5 连消耗点数")
    cost_11: int = Field(default=500, ge=0, description="11 连消耗点数")
    min_reward: int = Field(default=70, ge=0, description="每日签到最低点数")
    max_reward: int = Field(default=110, ge=0, description="每日签到最高点数")
    tz_offset_hours: int = Field(
        default=0,
        ge=-12,
        le=14,
        description="保留字段，日期与任务重置固定使用国际时间 UTC",
    )

    streak_daily_step: int = Field(default=10, ge=0, description="连续签到每天递增额外点数")
    streak_daily_max: int = Field(default=100, ge=0, description="连续签到每日额外点数上限")
    streak_weekly_reward: int = Field(default=600, ge=0, description="每连续签到 7 天额外奖励点数")
    streak_cycle_days: int = Field(default=15, ge=1, description="每连续签到多少天发放卡池周期奖励")
    streak_cycle_reward: int = Field(default=1200, ge=0, description="卡池周期奖励点数")
    non_gacha_checkin_probability: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="每日签到随机获得非抽卡卡的概率（0～1）",
    )
    savings_threshold_1: int = Field(default=1000, ge=0, description="囤点奖励第 1 档门槛")
    savings_bonus_1: int = Field(default=100, ge=0, description="囤点奖励第 1 档点数")
    savings_threshold_2: int = Field(default=3000, ge=0, description="囤点奖励第 2 档门槛")
    savings_bonus_2: int = Field(default=500, ge=0, description="囤点奖励第 2 档点数")
    savings_threshold_3: int = Field(default=10000, ge=0, description="囤点奖励第 3 档门槛")
    savings_bonus_3: int = Field(default=2000, ge=0, description="囤点奖励第 3 档点数")
    savings_bonus_reset_days: int = Field(
        default=60,
        ge=1,
        description="囤点档位奖励重置周期（天）",
    )

    @model_validator(mode="after")
    def _validate_ranges(self) -> "EconomyConfig":
        if self.max_reward < self.min_reward:
            raise ValueError("签到奖励 max_reward 不能小于 min_reward")
        thresholds = (self.savings_threshold_1, self.savings_threshold_2, self.savings_threshold_3)
        if thresholds != tuple(sorted(set(thresholds))):
            raise ValueError("囤点奖励门槛必须严格递增")
        if self.savings_bonus_reset_days < 1:
            raise ValueError("囤点奖励重置周期必须大于等于 1 天")
        return self


class MonthlyCardConfig(PluginConfigBase):
    """月卡配置。"""

    __ui_label__ = "月卡"
    __ui_icon__ = "calendar"
    __ui_order__ = 4

    price: int = Field(default=1000, ge=0, description="购买月卡消耗点数")
    duration_days: int = Field(default=30, ge=1, description="月卡有效天数")
    daily_bonus: int = Field(default=80, ge=0, description="月卡有效期每日签到额外点数")
    renew_max_remaining_days: int = Field(default=3, ge=0, description="剩余不超过多少天才能续费")
    half_price_5_pull_count: int = Field(default=2, ge=0, description="购买后赠送的半价五连次数")


class AdminConfig(PluginConfigBase):
    """管理员配置。"""

    __ui_label__ = "管理员"
    __ui_icon__ = "shield"
    __ui_order__ = 5

    admin_ids: list[int | str] = Field(
        default_factory=list,
        description="允许发放点数的管理员 QQ 号列表",
    )
    allow_local_operator: bool = Field(
        default=False,
        description="默认关闭；开启后本地操作用户也可视为管理员",
    )


class TaskConfig(PluginConfigBase):
    """随机任务配置。"""

    __ui_label__ = "随机任务"
    __ui_icon__ = "clipboard-check"
    __ui_order__ = 6

    enabled: bool = Field(default=True, description="是否启用随机任务")
    normal_count: int = Field(default=3, ge=1, description="每日普通任务次数")
    challenge_count: int = Field(default=2, ge=1, description="每日挑战任务次数")
    advanced_count: int = Field(default=1, ge=1, description="每日高级挑战任务次数")
    challenge_min_level: float = Field(
        default=10.0,
        ge=1.0,
        description="挑战最低等级，默认 10 级或以上",
    )
    advanced_min_level: float = Field(
        default=12.7,
        ge=1.0,
        description="高级挑战最低定数",
    )
    ultimate_min_level: float = Field(default=14.7, ge=1.0, description="终极最低定数")

    normal_reward: int = Field(default=35, ge=0, description="普通任务奖励")
    challenge_reward_s: int = Field(default=60, ge=0, description="挑战 S 奖励")
    challenge_reward_ss: int = Field(default=80, ge=0, description="挑战 SS 奖励")
    challenge_reward_sss: int = Field(default=100, ge=0, description="挑战 SSS/SSS+ 奖励")
    advanced_reward_s: int = Field(default=105, ge=0, description="高级挑战 S 奖励")
    advanced_reward_ss: int = Field(default=115, ge=0, description="高级挑战 SS 奖励")
    advanced_reward_sss: int = Field(default=125, ge=0, description="高级挑战 SSS/SSS+ 奖励")
    ultimate_reward: int = Field(default=30000, ge=0, description="终极任务奖励")

    require_photo: bool = Field(default=True, description="提交任务时是否要求图片")
    auto_reset: bool = Field(default=True, description="每日 00:00 自动过期未完成任务")
    auto_cleanup_history: bool = Field(
        default=True,
        description="每日自动清理已结束任务历史",
    )
    task_history_retention_days: int = Field(
        default=30,
        ge=1,
        description="已结束任务自动保留天数",
    )
    catalog_cache_ttl: int = Field(default=3600, ge=60, description="曲库缓存秒数")

    ongeki_source_url: str = Field(
        default="https://dp4p6x0xfi5o9.cloudfront.net/ongeki",
        description="音击 arcade-songs 数据源",
    )
    maimai_song_url: str = Field(
        default="https://maimai.lxns.net/api/v0/maimai/song/list?version=25500&notes=false",
        description="舞萌曲目接口",
    )
    chunithm_song_url: str = Field(
        default="https://maimai.lxns.net/api/v0/chunithm/song/list?version=23000&notes=false",
        description="中二曲目接口",
    )
    maimai_asset_url: str = Field(
        default="https://assets2.lxns.net/maimai",
        description="舞萌素材地址",
    )
    chunithm_asset_url: str = Field(
        default="https://assets2.lxns.net/chunithm",
        description="中二素材地址",
    )


class GrowthConfig(PluginConfigBase):
    """规则随静态数据版本固定，开关只控制新操作和产出。"""

    __ui_label__ = "角色养成"
    __ui_icon__ = "heart"
    __ui_order__ = 8

    enabled: bool = Field(default=True, description="启用全部17名主角色好感与主动解花；基础N卡默认1星")
    voice_enabled: bool = Field(default=True, description="启用已通过自动校验的QQ角色语音；全部素材已自动验收，可手动关闭")
    automatic_voice_enabled: bool = Field(default=True, description="语音启用后，成功送礼或好感升级自动回应；每笔最多一条")
    companion_points: int = Field(default=300, ge=1, description="每日陪伴获得的好感")
    gift_small_points: int = Field(default=300, ge=1, description="小礼物好感")
    gift_medium_points: int = Field(default=1000, ge=1, description="中礼物好感")
    gift_large_points: int = Field(default=10000, ge=1, description="大礼物好感")
    monthly_event_days: int = Field(default=7, ge=1, description="每月签到活动天数")
    monthly_event_small_gift_days: list[int] = Field(
        default_factory=lambda: [1, 3], description="活动周发放小礼物的日期"
    )
    monthly_event_medium_gift_days: list[int] = Field(
        default_factory=lambda: [5], description="活动周发放中礼物的日期"
    )
    monthly_event_large_gift_days: list[int] = Field(
        default_factory=lambda: [7], description="活动周发放大礼物的日期"
    )
    monthly_event_fragments: int = Field(default=5, ge=0, description="活动周其余日期发放的花之碎片")
    task_medium_gifts_daily_cap: int = Field(default=1, ge=0, description="任务中礼物每日上限")
    task_medium_gift_sources: list[str] = Field(
        default_factory=lambda: ["challenge", "advanced"], description="可发中礼物的任务类型"
    )
    task_fragments_normal: int = Field(default=1, ge=0, description="普通任务审核碎片")
    task_fragments_challenge: int = Field(default=1, ge=0, description="挑战任务审核碎片")
    task_fragments_advanced: int = Field(default=1, ge=0, description="高级挑战审核碎片")
    task_fragments_ultimate: int = Field(default=0, ge=0, description="终极任务审核碎片")
    task_fragments_daily_cap: int = Field(default=2, ge=0, description="任务碎片每日上限")
    gift_purchase_small_price: int = Field(default=150, ge=1, description="小礼物点数价格")
    gift_purchase_small_weekly_cap: int = Field(default=10, ge=1, description="小礼物每周购买上限")
    gift_purchase_medium_price: int = Field(default=500, ge=1, description="中礼物点数价格")
    gift_purchase_medium_weekly_cap: int = Field(default=3, ge=1, description="中礼物每周购买上限")
    bloom_items: list[str] = Field(
        default_factory=lambda: ["bloom_ticket", "flower_fragment"],
        description="解花/超解花消耗的物品ID",
    )
    bloom_levels: list[int] = Field(default_factory=lambda: [100, 200], description="解花/超解花好感等级")
    bloom_costs: list[int] = Field(default_factory=lambda: [1, 90], description="解花/超解花物品数量")
    bloom_ticket_source_kind: str = Field(default="advanced", description="解花券来源任务类型")
    bloom_ticket_source_min_level: float = Field(default=13.5, ge=1.0, description="解花券来源目标最低定数")
    bloom_ticket_source_grade: str = Field(default="SSS", description="解花券来源最低评级")
    bloom_ticket_cooldown_days: int = Field(default=15, ge=0, description="解花券获取冷却天数")
    ultimate_large_gifts_lifetime_cap: int = Field(default=0, ge=0, description="终极任务大礼物终身上限")
    voice_cooldown_seconds: int = Field(default=10, ge=0, description="语音点播冷却秒数")
    voice_minute_limit: int = Field(default=5, ge=1, description="语音每分钟次数上限")
    voice_request_ttl_seconds: int = Field(default=600, ge=1, description="语音请求去重保留秒数")
    voice_inflight_limit: int = Field(default=10, ge=1, description="单会话并发送语音上限")
    voice_send_timeout_seconds: int = Field(default=30, ge=1, description="单条语音发送超时秒数")
    rules_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="高级覆盖 rules_draft.json；留空表示使用上方具名配置与随包规则",
    )

    @model_validator(mode="after")
    def _validate_growth_rules(self) -> "GrowthConfig":
        if len(self.bloom_levels) != 2 or self.bloom_levels[0] > self.bloom_levels[1]:
            raise ValueError("bloom_levels 必须为两个递增等级")
        if self.bloom_levels[1] > 1000:
            raise ValueError("bloom_levels 不能超过 Lv1000 奖励终点")
        if len(self.bloom_costs) != 2 or min(self.bloom_costs) < 1:
            raise ValueError("bloom_costs 必须为两个正整数")
        allowed_items = {"gift_small", "gift_medium", "gift_large", "flower_fragment", "bloom_ticket"}
        if len(self.bloom_items) != 2 or not set(self.bloom_items) <= allowed_items:
            raise ValueError("bloom_items 必须为两个已知物品ID")
        if self.bloom_ticket_source_kind not in {"advanced"}:
            raise ValueError("bloom_ticket_source_kind 目前只支持 advanced")
        if self.bloom_ticket_source_grade not in {"SSS", "SSS+"}:
            raise ValueError("bloom_ticket_source_grade 只能是 SSS 或 SSS+")
        if any(day > self.monthly_event_days for day in (
            *self.monthly_event_small_gift_days,
            *self.monthly_event_medium_gift_days,
            *self.monthly_event_large_gift_days,
        )):
            raise ValueError("月度活动物品日期不能超过 monthly_event_days")
        allowed = {"normal", "challenge", "advanced", "ultimate"}
        if not self.task_medium_gift_sources or not set(self.task_medium_gift_sources) <= allowed:
            raise ValueError("task_medium_gift_sources 含未知任务类型")
        return self

    def rule_overrides(self) -> dict[str, Any]:
        """返回注入 GrowthCatalog 的规则覆盖；显式字段优先于 rules_overrides。"""
        values: dict[str, Any] = dict(self.rules_overrides or {})
        values.update({
            "companion_points": self.companion_points,
            "gift_points": {
                "small": self.gift_small_points,
                "medium": self.gift_medium_points,
                "large": self.gift_large_points,
            },
            "monthly_event_days": self.monthly_event_days,
            "monthly_event_small_gift_days": list(self.monthly_event_small_gift_days),
            "monthly_event_medium_gift_days": list(self.monthly_event_medium_gift_days),
            "monthly_event_large_gift_days": list(self.monthly_event_large_gift_days),
            "monthly_event_fragments": self.monthly_event_fragments,
            "task_medium_gifts_daily_cap": self.task_medium_gifts_daily_cap,
            "task_medium_gift_sources": list(self.task_medium_gift_sources),
            "task_fragments_daily_cap": self.task_fragments_daily_cap,
            "task_fragments": {
                "normal": self.task_fragments_normal,
                "challenge": self.task_fragments_challenge,
                "advanced": self.task_fragments_advanced,
                "ultimate": self.task_fragments_ultimate,
            },
            "gift_purchase": {
                "small": {"price": self.gift_purchase_small_price, "weekly_cap": self.gift_purchase_small_weekly_cap},
                "medium": {"price": self.gift_purchase_medium_price, "weekly_cap": self.gift_purchase_medium_weekly_cap},
            },
            "bloom_items": list(self.bloom_items),
            "bloom_levels": list(self.bloom_levels),
            "bloom_costs": list(self.bloom_costs),
            "bloom_ticket_source": {
                "kind": self.bloom_ticket_source_kind,
                "min_level": self.bloom_ticket_source_min_level,
                "grade": self.bloom_ticket_source_grade,
                "cooldown_days": self.bloom_ticket_cooldown_days,
            },
            "ultimate_large_gifts_lifetime_cap": self.ultimate_large_gifts_lifetime_cap,
        })
        return values


class UIConfig(PluginConfigBase):
    """图片缓存与短回复阈值；原先写死在 plugin.py。"""

    __ui_label__ = "界面与缓存"
    __ui_icon__ = "image"
    __ui_order__ = 9

    render_cache_ttl_seconds: int = Field(default=24 * 60 * 60, ge=60, description="渲染临时文件保留秒数")
    pool_image_cache_ttl_seconds: int = Field(default=7 * 24 * 60 * 60, ge=60, description="卡池公告图缓存秒数")
    max_pool_image_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, description="卡池公告图单张最大字节数")
    short_reply_max_lines: int = Field(default=10, ge=1, description="短回复直接发文字的最大行数")
    short_reply_max_chars: int = Field(default=700, ge=1, description="短回复直接发文字的最大字符数")


class OngekiGachaPluginConfig(PluginConfigBase):
    """ONGEKI 模拟抽卡插件配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    assets: AssetsConfig = Field(default_factory=AssetsConfig)
    pool: PoolConfig = Field(default_factory=PoolConfig)
    economy: EconomyConfig = Field(default_factory=EconomyConfig)
    monthly_card: MonthlyCardConfig = Field(default_factory=MonthlyCardConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
    task: TaskConfig = Field(default_factory=TaskConfig)
    growth: GrowthConfig = Field(default_factory=GrowthConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
