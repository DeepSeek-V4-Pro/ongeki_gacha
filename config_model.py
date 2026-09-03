"""插件配置模型。"""

from __future__ import annotations

from maibot_sdk import Field, PluginConfigBase


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="1.0.0", description="配置版本")


class AssetsConfig(PluginConfigBase):
    """本地素材路径配置。"""

    __ui_label__ = "素材路径"
    __ui_icon__ = "folder-open"
    __ui_order__ = 1

    cards_dir: str = Field(
        default="D:/Tools/ONGEKI_unpack/output/cards_2690",
        description="卡面 PNG 目录，包含 ui_card_*.png",
    )
    card_info_json: str = Field(
        default="D:/Tools/ONGEKI_unpack/output/card_info_merged.json",
        description="卡牌信息 JSON 文件",
    )


class PoolConfig(PluginConfigBase):
    """全卡大混池概率权重。"""

    __ui_label__ = "概率权重"
    __ui_icon__ = "dices"
    __ui_order__ = 2

    weight_n: int = Field(default=1, description="N 权重（彩蛋档）")
    weight_r: int = Field(default=76, description="R 权重")
    weight_sr: int = Field(default=19, description="SR 权重")
    weight_sr_plus: int = Field(default=1, description="SR+ 权重（彩蛋档）")
    weight_ssr: int = Field(default=3, description="SSR 权重")


class EconomyConfig(PluginConfigBase):
    """点数、签到与消耗配置。"""

    __ui_label__ = "点数"
    __ui_icon__ = "coins"
    __ui_order__ = 3

    cost_1: int = Field(default=50, description="1 连消耗点数")
    cost_5: int = Field(default=250, description="5 连消耗点数")
    cost_11: int = Field(default=500, description="11 连消耗点数")
    min_reward: int = Field(default=400, description="每日签到最低点数")
    max_reward: int = Field(default=800, description="每日签到最高点数")
    tz_offset_hours: int = Field(default=8, description="签到北京时间 UTC 偏移小时数")


class OngekiGachaPluginConfig(PluginConfigBase):
    """ONGEKI 模拟抽卡插件配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    assets: AssetsConfig = Field(default_factory=AssetsConfig)
    pool: PoolConfig = Field(default_factory=PoolConfig)
    economy: EconomyConfig = Field(default_factory=EconomyConfig)
