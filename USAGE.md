# 使用说明

## 简介

音击抽卡模拟器是一个 MaiBot 插件，用于在聊天环境中模拟音击卡牌抽卡、收藏和成长体验。

## 安装

1. 将插件目录放入 MaiBot 的插件目录。
2. 在插件管理界面加载或重启 MaiBot。
3. 插件默认读取自身目录下的 `assets/card_data/`。

如果卡面素材未随插件提供，请先运行数据同步脚本，或在插件配置中指定已有数据的绝对路径。

## 数据目录

默认数据目录：

```text
assets/card_data/
```

其中包含：

- `card_info_merged.json`：卡牌元数据。
- `card_data_manifest.json`：文件大小和 SHA-256 校验清单。
- `ui_card_*.png`：卡面素材，通常不随代码仓库分发。

可通过插件配置中的 `assets.cards_dir` 和 `assets.card_info_json` 切换到其他数据目录。

## 数据同步与校验

已有本地素材时，运行：

```powershell
python sync_card_data.py --dry-run
python sync_card_data.py
python sync_card_data.py --check
```

素材不在默认位置时，指定来源：

```powershell
python sync_card_data.py `
  --source D:/path/to/cards `
  --source-json D:/path/to/card_info_merged.json
```

`--check` 会校验文件大小与 SHA-256；`--quick` 只检查文件是否存在。

## 命令

| 命令 | 说明 |
| --- | --- |
| `/签到` | 每日随机领取点数，可能触发额外奖励 |
| `/抽卡 1` | 单抽 |
| `/抽卡 5` | 五连，每用户每周首次包含 SR 或以上保底 |
| `/抽卡 11` | 十一连，始终包含 SR 或以上保底 |
| `/点数` | 查看当前点数 |
| `/卡册` | 查看收藏进度 |
| `/卡图 <ID>` | 发送已拥有卡牌的高清原图 |
| `/帮助` | 显示命令列表 |

所有点数均为模拟货币，不代表真实游戏资源。

## 临时文件清理

抽卡结果临时图片保留 24 小时。插件加载时会清理过期文件，运行期间每天自动清理一次。

## 故障排查

- 加载失败并提示数据不可用：运行 `sync_card_data.py`，或在配置中填写正确的绝对路径。
- 卡图命令提示未拥有：该命令只允许查询当前玩家已获得的卡牌。
- 签到重复提示：同一日期只能签到一次。

更多限制请阅读 [DISCLAIMER.md](DISCLAIMER.md)。
