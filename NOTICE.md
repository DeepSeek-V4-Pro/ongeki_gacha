# 第三方与素材声明

## 1. 分发范围

本项目的仓库与发布包**只包含代码、文档、工具和 JSON 索引，不包含任何游戏素材**：
卡面、角色立绘、界面图片、字体与音频都由使用者在本地用 `tools/` 下的工具接入。
接入步骤见 [素材指南](ASSETS.md)。

所以本文列出的素材条目描述的是**这些素材在功能上的用途与整理方式**，
不代表它们随项目分发，也不构成任何授权。

## 2. 上游项目

### AquaViewer / RinNET

抽卡展示、图层处理和部分资源命名参考了 AquaViewer / RinNET 相关内容。
上游以 GNU Affero General Public License v3 授权，完整文本见 [LICENSE](LICENSE)；
使用、修改或分发相关部分时请遵守 AGPL-3.0 及上游许可要求。

### Artemis

卡池权重与静态卡池定义参考了 Artemis 的
`titles/cm/cm_data/MU3/static_gachas.csv` 与 `static_gacha_cards.csv`。
Artemis 以 GNU Affero General Public License v3 授权；本项目只从公开数据中提取
卡池排期与权重字段，不以复制其运行代码为目的。

### MaiBot / MaiBot SDK

本项目作为 MaiBot 插件运行，需要外部提供 MaiBot SDK 与运行环境；
SDK 及其依赖不属于本项目，请遵循其各自许可。

## 3. SEGA 与第三方游戏素材

音击相关的名称、商标、角色、卡面、UI、音频及其他素材，权利均归 SEGA 或相应权利人所有，
**不随 AGPL 许可证授权**。项目代码的开源许可只覆盖项目自有内容。

以下条目说明各功能用到的素材种类、整理方式与来源记录位置；
`assets/growth/visual_asset_manifest.json`、`assets/growth/*_catalog.json` 等清单
逐项记录了来源、尺寸与 SHA-256，用于本地核对。

| 功能 | 素材种类 | 整理与记录 |
| --- | --- | --- |
| 抽卡卡面 | 标准卡面（768×1052）与卡池公告图 | 卡面用 [素材指南](ASSETS.md) 导入；公告图由 `/卡池` 从官方公开页面下载并缓存 |
| 抽卡结果图 | 星级、MAX、解花印记与数字字体 | 手动放入 `assets/ui/` |
| 卡牌揭示图 | 原作 CardGet 资源组（`SB_CMN_CardGet_*`）、稀有度 Logo、属性图标与卡框 | `tools/build_growth_assets.py` 按白名单打包 |
| 好感页 | 教室背景、栏目底板、可伸展面板、好感心形量表与数字图集 | 同上，清单见 `visual_asset_manifest.json` |
| 好感立绘 | 主角色基础 Q 版图，以及官网 `image_normal.png` 原始立绘 | 同上；官网角色 ID 与游戏角色 ID 不同，清单逐名记录两种 ID |
| 解花 | 解花/超解花标题字、箭头、花瓣、光效与完成底条 | 同上，另有 `assets/growth/images/bloom/manifest.json` |
| 物品获得图 | 礼物图标、花之碎片花瓣素材、解花券图标（`UI_Item_OpenFlower_Ticket_00`） | 同上 |
| 档案奖励 | 名牌与名牌图标、装饰预览、称号底图与图标 | 称号按原作稀有度共用底图并叠加文字；装饰只使用原作预览，不打包模型与粒子数据 |
| 角色语音 | 17 名主角色档案语音 170 条、事件语音 51 条 | 用 `tools/build_affection_voice_assets.py`、`build_event_voice_assets.py` 打包，散列见 `assets/growth/*_catalog.json` |

上述素材仅用于本非官方、非商业的收藏模拟项目；来源标注不代表获得官方授权，
也不会把游戏美术重新授予开源许可。

## 4. 字体

渲染优先使用 Noto Sans CJK SC Regular / Bold（notofonts/noto-cjk，
按 SIL Open Font License 1.1 授权）。**字体同样不随包分发**，
缺失时插件会回退到系统中文字体；`assets/fonts/LICENSE` 与 `sources.json`
记录了许可文本与下载地址摘要，可用于自行获取后放入 `assets/fonts/`。

## 5. 权利与联系

如你是相关权利人并希望停止使用特定素材，请通过仓库提供的联系方式联系维护者，
我们会在确认后尽快处理。具体的责任范围见 [DISCLAIMER.md](DISCLAIMER.md)。
