"""插件配置模型。"""

from __future__ import annotations

from maibot_sdk import Field, PluginConfigBase
from pydantic import model_validator


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="1.2.1", description="配置版本")


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
        description="official=按官方日期选池；cycle=按间隔循环历史官方池",
    )
    rotation_interval_days: int = Field(
        default=15,
        ge=1,
        description="cycle 模式下每个池的持续天数",
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
    min_reward: int = Field(default=100, ge=0, description="每日签到最低点数")
    max_reward: int = Field(default=250, ge=0, description="每日签到最高点数")
    tz_offset_hours: int = Field(
        default=0,
        ge=-12,
        le=14,
        description="保留字段，日期与任务重置固定使用国际时间 UTC",
    )

    streak_daily_step: int = Field(default=10, ge=0, description="连续签到每天递增额外点数")
    streak_daily_max: int = Field(default=100, ge=0, description="连续签到每日额外点数上限")
    streak_weekly_reward: int = Field(default=500, ge=0, description="每连续签到 7 天额外奖励点数")
    streak_cycle_days: int = Field(default=15, ge=1, description="每连续签到多少天发放卡池周期奖励")
    streak_cycle_reward: int = Field(default=1000, ge=0, description="卡池周期奖励点数")
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

    price: int = Field(default=1500, ge=0, description="购买月卡消耗点数")
    duration_days: int = Field(default=30, ge=1, description="月卡有效天数")
    daily_bonus: int = Field(default=100, ge=0, description="月卡有效期每日签到额外点数")
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
    normal_count: int = Field(default=5, ge=1, description="每日普通任务次数")
    challenge_count: int = Field(default=3, ge=1, description="每日挑战任务次数")
    challenge_min_level: float = Field(
        default=10.0,
        ge=1.0,
        description="挑战最低等级，默认 10 级或以上",
    )
    ultimate_min_level: float = Field(default=14.7, ge=1.0, description="终极最低定数")

    normal_reward: int = Field(default=20, ge=0, description="普通任务奖励")
    challenge_reward_s: int = Field(default=30, ge=0, description="挑战 S 奖励")
    challenge_reward_ss: int = Field(default=40, ge=0, description="挑战 SS 奖励")
    challenge_reward_sss: int = Field(default=50, ge=0, description="挑战 SSS/SSS+ 奖励")
    ultimate_reward: int = Field(default=30000, ge=0, description="终极任务奖励")

    exclude_special: bool = Field(
        default=True,
        description="排除 LUNATIC / 宴会场 / WORLD'S END 等特殊谱面",
    )
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


class OngekiGachaPluginConfig(PluginConfigBase):
    """ONGEKI 模拟抽卡插件配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    assets: AssetsConfig = Field(default_factory=AssetsConfig)
    pool: PoolConfig = Field(default_factory=PoolConfig)
    economy: EconomyConfig = Field(default_factory=EconomyConfig)
    monthly_card: MonthlyCardConfig = Field(default_factory=MonthlyCardConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
    task: TaskConfig = Field(default_factory=TaskConfig)
