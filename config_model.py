"""插件配置模型。"""

from __future__ import annotations

from maibot_sdk import Field, PluginConfigBase


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="1.1.0", description="配置版本")


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

    weight_n: int = Field(default=1, description="N 权重")
    weight_r: int = Field(default=76, description="R 权重")
    weight_sr: int = Field(default=19, description="SR 权重")
    weight_sr_plus: int = Field(default=1, description="SR+ 权重")
    weight_ssr: int = Field(default=3, description="SSR 权重")
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
        description="cycle 模式下每个池的持续天数",
    )
    pickup_multiplier: int = Field(
        default=10,
        description="UP 卡相对普通卡的权重倍率",
    )
    strict_pool_cards: bool = Field(
        default=False,
        description="True 时仅使用排表中的 UP/选择卡，False 时保留版本全卡基础池",
    )


class EconomyConfig(PluginConfigBase):
    """点数、签到与消耗配置。"""

    __ui_label__ = "点数"
    __ui_icon__ = "coins"
    __ui_order__ = 3

    cost_1: int = Field(default=50, description="1 连消耗点数")
    cost_5: int = Field(default=250, description="5 连消耗点数")
    cost_11: int = Field(default=500, description="11 连消耗点数")
    min_reward: int = Field(default=100, description="每日签到最低点数")
    max_reward: int = Field(default=250, description="每日签到最高点数")
    tz_offset_hours: int = Field(default=8, description="签到北京时间 UTC 偏移小时数")

    streak_daily_step: int = Field(default=10, description="连续签到每天递增额外点数")
    streak_daily_max: int = Field(default=100, description="连续签到每日额外点数上限")
    streak_weekly_reward: int = Field(default=500, description="每连续签到 7 天额外奖励点数")
    streak_cycle_days: int = Field(default=15, description="每连续签到多少天发放卡池周期奖励")
    streak_cycle_reward: int = Field(default=1000, description="卡池周期奖励点数")
    savings_threshold_1: int = Field(default=1000, description="囤点奖励第 1 档门槛")
    savings_bonus_1: int = Field(default=100, description="囤点奖励第 1 档点数")
    savings_threshold_2: int = Field(default=3000, description="囤点奖励第 2 档门槛")
    savings_bonus_2: int = Field(default=500, description="囤点奖励第 2 档点数")
    savings_threshold_3: int = Field(default=10000, description="囤点奖励第 3 档门槛")
    savings_bonus_3: int = Field(default=2000, description="囤点奖励第 3 档点数")


class MonthlyCardConfig(PluginConfigBase):
    """月卡配置。"""

    __ui_label__ = "月卡"
    __ui_icon__ = "calendar"
    __ui_order__ = 4

    price: int = Field(default=1500, description="购买月卡消耗点数")
    duration_days: int = Field(default=30, description="月卡有效天数")
    daily_bonus: int = Field(default=100, description="月卡有效期每日签到额外点数")
    renew_max_remaining_days: int = Field(default=3, description="剩余不超过多少天才能续费")
    half_price_5_pull_count: int = Field(default=2, description="购买后赠送的半价五连次数")


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


class OngekiGachaPluginConfig(PluginConfigBase):
    """ONGEKI 模拟抽卡插件配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    assets: AssetsConfig = Field(default_factory=AssetsConfig)
    pool: PoolConfig = Field(default_factory=PoolConfig)
    economy: EconomyConfig = Field(default_factory=EconomyConfig)
    monthly_card: MonthlyCardConfig = Field(default_factory=MonthlyCardConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
