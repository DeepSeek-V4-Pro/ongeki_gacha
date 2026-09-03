# ONGEKI 模拟抽卡

基于 Salt 实例 `D:/MaiBotOneKey/instances/salt/MaiBot` 的 MaiBot 插件 SDK 编写的本地娱乐抽卡插件。

## 功能

| 命令 | 说明 |
| --- | --- |
| `/签到` | 每日领取 400～800 点 |
| `/抽卡 1` | 单抽，50 点 |
| `/抽卡 5` | 五连，250 点，保证至少 1 张 SR 以上 |
| `/抽卡 11` | 十一连，500 点，保证至少 1 张 SR 以上 |
| `/点数` | 查看当前点数 |
| `/卡册` | 查看收藏进度 |
| `/概率` | 查看当前模拟权重 |
| `/帮助` | 查看命令列表 |

同时支持 `/og 抽卡 11`、`/og签到`、`/og 点数`、`/og卡册` 等别名。

## 数据与素材

插件默认读取：

- 卡面目录：`D:/Tools/ONGEKI_unpack/output/cards_2690`
- 卡牌信息：`D:/Tools/ONGEKI_unpack/output/card_info_merged.json`
- 玩家数据库：Salt 实例 `data/plugins/deepseek-v4-pro.ongeki-gacha/ongeki_gacha.db`

素材路径可在插件配置中的 `assets` 分区修改。若移动 ONGEKI 素材目录，需要同步修改配置并重新加载插件。

## 概率说明

V1 全卡大混池的默认权重为：

| 稀有度 | 权重 |
| --- | ---: |
| N | 1 |
| R | 76 |
| SR | 19 |
| SR+ | 1 |
| SSR | 3 |

权重来自项目设计文档中的本地娱乐参考值，不是 SEGA 官方概率，也不代表任何真实抽卡结果。

## 文件结构

```text
ongeki_gacha/
├─ _manifest.json
├─ plugin.py
├─ config_model.py
├─ gacha_core.py
├─ gacha_db.py
├─ gacha_render.py
├─ assets/ui/
└─ ...
```

## 部署

插件已直接放入 Salt 实例：

```text
D:/MaiBotOneKey/instances/salt/MaiBot/plugins/ongeki_gacha/
```

在 MaiBot 插件管理界面重新加载或重启 Salt 实例后即可生效。

## 待确认

- `_manifest.json` 的 `urls.repository` 目前使用作者主页作为临时占位，仓库确定后请替换。
- 素材目录位于 `D:/Tools/ONGEKI_unpack`，当前部署是本地绝对路径；跨机器部署时需要把素材放到实例可访问的位置并修改配置。
