# 使用说明

当前版本：1.1.0

## 简介

音击抽卡模拟器是一个 MaiBot 插件，用于在聊天环境中模拟音击卡牌抽卡、收藏和成长体验。

抽卡结果图底部会按 RinNET 前端样式叠加一条黑色半透明信息条，显示卡牌数字 ID 和完整卡号（例如 `104490 [O.N.G.E.K.I.]1.50-E-0371`）；`/卡图` 发送的是原始高清卡面，不会叠加这条信息。

## 安装

1. 将插件目录放入 MaiBot 的插件目录。
2. 在插件管理界面加载或重启 MaiBot。
3. 插件默认读取自身目录下的 `assets/card_data/`。

如果卡面素材未随插件提供，请先运行数据同步脚本，或在插件配置中指定已有数据的绝对路径。

卡面素材的来源、公开可查询方式和使用权限说明，请阅读
[CARD_ARTWORK_SOURCES.md](CARD_ARTWORK_SOURCES.md)。

## 数据目录

默认数据目录：

```text
assets/card_data/
```

其中包含：

- `card_info_merged.json`：卡牌元数据。
- `card_data_manifest.json`：文件大小和 SHA-256 校验清单。
- `gacha_pools.json`：官方 CARDMAKER 卡池排表（63 个池，2020-10～2026-07），每个池由“当期版本已有全部 R/SR/SSR 基础卡 + 官方 UP 标记与天井选择名单”组成，每张卡附带 `id / version / cardNumber`、UP/选择标记和非抽卡掉落池。
- `ui_card_*.png`：卡面素材，通常不随代码仓库分发。

可通过插件配置中的 `assets.cards_dir` 和 `assets.card_info_json` 切换到其他数据目录。
通过配置界面修改后会立即热更新卡牌数据、概率、签到、月卡和管理员配置；直接编辑 `config.toml` 后需要重新加载插件。

## 卡池与轮替

- `[pool] rotation_mode = "cycle"`：按 `rotation_interval_days`（默认 15 天）循环使用 63 个历史官方卡池。
- `[pool] rotation_mode = "official"`：按官方公告日期选池；若当天没有活动池则使用常驻池（当前版本已有全部 R/SR/SSR）。
- 与原游戏卡牌机的规则一致：活动池不会只有公告列出的新卡/选择卡，而是按活动日期过滤出该版本已存在的全部 R/SR/SSR，再叠加上本期 UP 与天井选择卡；UP 卡按 `pickup_multiplier`（默认 ×10）加权。
- `strict_pool_cards = true`：严格按池子候选卡列表抽取；排表 `cards` 已经包含“当期版本基础卡 + UP/天井选择标记”，不会把未在当期出现的未来版本卡混入。
- 可通过 `/卡池` 查看当前池、历史轮替进度、天井信息，并同时发送 SEGA 官方活动图（网络不可用时只显示文字）。
- `/卡池` 会展示当前池 UP SSR 角色预览、UP/天井选择数量；`/卡池 列表` 会以转发消息列出全部 UP SSR。
- 官方公告未标注 UP 的池会显示 `UP 卡：0 张`，但候选仍是当期版本已有全部 R/SR/SSR；天井选择卡只表示可兑换名单，不代表抽卡候选只有这些卡。
- 常驻池包含当前版本已有的全部 R/SR/SSR 基础卡（包括历史活动卡）；可先 `/抽卡 常驻 <1/5/11>` 抽取，`/抽卡` 默认仍是当前轮替活动池。
- `gacha_pools.json` 的 `non_gacha_pool` 卡不会进入任何抽卡池，改为每日签到按概率随机掉落。
- 天井按卡池独立累计：每次抽取一张卡 +1 点；达到 `select_points` 上限后可用 `/天井 <卡ID>` 兑换当前池的可选卡，每个池只能兑换一次。
- `/天井列表`（或 `/天井 列表`）可查看当前池全部可选卡的 ID 与角色；池轮换后旧池天井不会带入新池。
- 帮助只保留命令列表；详细规则使用 `/规则` 转发查看，较长列表自动转为转发消息。

排表来源：SEGA 官方 CARDMAKER 公告 + Artemis `static_gachas.csv` / `static_gacha_cards.csv`。

## 数据同步与校验

已有本地素材时，运行（默认从插件自身的 `assets/card_data/` 读取并生成校验清单）：

```powershell
python sync_card_data.py --dry-run
python sync_card_data.py
python sync_card_data.py --check
```

素材不在默认位置时，指定来源：

```powershell
python sync_card_data.py `
  --source ./path/to/cards `
  --source-json ./path/to/card_info_merged.json
```

`--check` 会校验文件大小与 SHA-256；`--quick` 只检查文件是否存在。

## 命令

| 命令 | 说明 |
| --- | --- |
| `/签到` | 每日领取基础点数，并结算连续签到、7 天/15 天周期和月卡加成 |
| `/抽卡 1` | 单抽 |
| `/抽卡 常驻 1` | 从常驻池单抽；也支持 `/抽卡 常驻 5`、`/抽卡 常驻 11` |
| `/抽卡 5` | 五连，每用户每周首次包含 SR 或以上保底 |
| `/抽卡 11` | 十一连，始终包含 SR 或以上保底 |
| `/点数` | 查看当前点数；点数达到囤点门槛时自动发放对应奖励 |
| `/月卡` | 查看月卡状态；发送 `/月卡 购买` 购买，`/月卡 续费` 续费 |
| `/概率` | 查看当前模拟权重、消耗和保底规则 |
| `/卡池` | 查看本期卡池、UP SSR 角色、UP/天井选择数量与轮替进度，并发送官方活动图 |
| `/天井` | 查看当前卡池天井进度；满后可 `/天井 <卡ID>` 兑换选择卡 |
| `/天井列表` | 独立查看当前池全部天井选择卡的 ID 与角色；也可使用 `/天井 列表` |
| `/规则` | 以转发消息形式查看完整奖励、抽卡、天井和月卡规则 |
| `/卡册` | 查看收藏进度 |
| `/卡图 <ID>` | 发送已拥有卡牌的高清原图 |
| `/奖励 @用户 <点数> [备注]` | 管理员向指定用户发放点数；也可直接填写 QQ 号 |
| `/帮助` | 显示精简命令列表；详细规则请发送 `/规则` |

所有点数均为模拟货币，不代表真实游戏资源。

## 连续签到与月卡

- 连续签到每天递增奖励：第 2 天起每天额外 +10 点，最高每日额外 +100 点；断签后重新从第 1 天计算。
- 连续签到第 7 天、14 天、21 天等额外获得 500 点。
- 连续签到第 15、30、45 天等同额外获得卡池周期奖励，默认 1000 点；第 15 天刚好对应一次 15 天猫池轮替。
- 每日基础签到默认随机获得 100～250 点，平均约 175 点；连续签到、卡池周期、月卡、管理员发放等奖励在此基础上累加。
- 每日签到有 `non_gacha_checkin_probability`（默认 5%）概率随机获得一张 `non_gacha_pool` 中的非抽卡卡。
- 购买月卡消耗 1500 点，有效期 30 天，有效期内每日签到额外获得 100 点。
- 月卡剩余不超过 3 天才能续费，购买后获得两次半价五连（默认 2 次）。
- 点数达到 1000 / 3000 / 10000 时，签到或查看点数会自动领取 100 / 500 / 2000 点囤点奖励；每个档位只触发一次。
- 以上数值均可在配置中调整：`[economy]` 下的连续签到、卡池周期与囤点项，以及 `[monthly_card]` 下的月卡项。

这种结构会让每天都能抽 11 连的人减少，但长期签到、囤积点数、购买月卡的人会有更明显的周期收益；攒到 500 点再抽 11 连的性价比也更高。

## 管理员发放

- 在 `[admin]` 配置中将管理员 QQ 号加入 `admin_ids` 列表。
- 管理员可使用 `/奖励 @用户 <点数> [备注]` 或 `/奖励 <QQ号> <点数> [备注]` 为指定用户增加点数。
- `allow_local_operator` 默认关闭；需要本地操作用户也可发放点数时手动开启，并加入管理员 QQ 白名单。
- 每次发放会写入 `admin_grants` 表，方便后续核对。

## 配置项

默认配置见 `config.toml`，所有数值均可在配置界面或文件中调整。

```toml
[economy]
cost_1 = 50
cost_5 = 250
cost_11 = 500
min_reward = 100
max_reward = 250
tz_offset_hours = 8
streak_daily_step = 10
streak_daily_max = 100
streak_weekly_reward = 500
streak_cycle_days = 15
streak_cycle_reward = 1000
non_gacha_checkin_probability = 0.05
savings_threshold_1 = 1000
savings_bonus_1 = 100
savings_threshold_2 = 3000
savings_bonus_2 = 500
savings_threshold_3 = 10000
savings_bonus_3 = 2000

[pool]
weight_n = 0
weight_r = 77
weight_sr = 20
weight_sr_plus = 0
weight_ssr = 3
schedule_json = "assets/card_data/gacha_pools.json"
rotation_mode = "cycle"
rotation_interval_days = 15
pickup_multiplier = 10
strict_pool_cards = true

[monthly_card]
price = 1500
duration_days = 30
daily_bonus = 100
renew_max_remaining_days = 3
half_price_5_pull_count = 2

[admin]
admin_ids = []
allow_local_operator = false
```

## 临时文件清理

抽卡结果临时图片保留 24 小时。插件加载时会清理过期文件，运行期间每天自动清理一次。

## 故障排查

- 加载失败并提示数据不可用：运行 `sync_card_data.py`，或在配置中填写正确的绝对路径。
- `/卡池` 显示空排表：确认 `assets/card_data/gacha_pools.json` 存在。
  插件发布包随附该排表；如果排表损坏或丢失，请重新获取插件包。
- `/卡池` 显示 `UP 卡：0 张`：官方公告没有提供 UP 名单时属于正常；可发送 `/天井列表` 查看天井选择卡，发送 `/概率` 查看实际稀有度权重。
- 卡图命令提示未拥有：该命令只允许查询当前玩家已获得的卡牌。
- 签到重复提示：同一日期只能签到一次。

更多限制请阅读 [DISCLAIMER.md](DISCLAIMER.md)。
