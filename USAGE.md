# 使用说明

当前版本：1.3.4（2026-10-10）

本文结构：1 简介｜2 安装、素材与升级｜3 命令总览｜4 抽卡、卡池与天井｜
5 点数经济｜6 随机任务｜7 主角色好感养成｜8 显示与回退｜9 数据目录与素材｜
10 配置项｜11 故障排查｜12 已知限制。素材怎么接入见 [素材指南](ASSETS.md)，
本版新增与修复见 [更新日志](CHANGELOG.md)。

## 曲目预览（1.3.4）

- `/曲目预览 [音击|舞萌|中二] 曲名 [| 歌手]`：发送游戏曲绘原图与 QQ 语音，正常成功不额外发送说明文字。
- 多个候选时发送图片列表；同一账号在同一会话 **60 秒内**发送 `/曲目预览 序号`，或 `/曲目预览 取消`。过期须重新搜索。
- 只搜索已接入的三个游戏曲库；歌曲和歌手版本必须对应，曲绘必须来自该游戏曲库。不会用唱片封面替代游戏曲绘。
- 接取终极任务成功后自动发送对应歌曲语音，失败静默，不影响任务接取。手动预览会显示失败环节和原因。
- 优先发送完整音频，不按 30/60 秒截断；默认最长 20 分钟，超过上限会报错，可配置。QQ 最终能否接收长语音仍取决于适配器。

曲目预览另需 FFmpeg（安装 `imageio-ffmpeg`，或配置 `preview.ffmpeg_path`）；多音源、登录信息与排错见 [曲目预览配置](MUSIC_PREVIEW.md)。

## 1. 简介

音击抽卡模拟器是一个 MaiBot 插件，在聊天环境里模拟音击（オンゲキ）的抽卡、卡牌收藏、
随机任务和主角色好感养成。所有点数与物品都是模拟数值，不接入官方玩家账号或机台数据。
运行时会获取公开曲库、曲绘和官方卡池公告图；卡牌素材与玩家存档保存在本地。

- 抽卡结果图底部带 RinNET 风格的信息条，显示卡牌数字 ID 与完整卡号（如 `104490 [O.N.G.E.K.I.]1.50-E-0371`）；
  `/卡图` 发送的是不带信息条的高清原图。
- SSR 抽到后会在结果图之外额外发送一张卡牌揭示图；基础 N 卡首获与好感升星、签到彩蛋卡也用同一版式。揭示图显示解花状态对应的 MAX Lv 和 MAX 攻击力，暂不显示技能。
- 规则、卡池、卡册、任务列表等长内容排版为分页图片卡片，短回执直接发文字；渲染或发送失败时自动回退纯文本。

## 2. 安装与升级

1. 把插件目录放进 MaiBot 插件目录（新版运行时为 `<MaiBot>/plugins/`）。
2. 在插件管理界面加载该插件，或重启实例。
3. 插件默认读取自身目录下的 `assets/card_data/`，也可以在配置中填写绝对路径。
4. 需要 MaiBot SDK 2.x 与 Pillow >= 11.3.0；首次加载会自动生成 `config.toml`。

### 2.1 素材是可选的

发布包**只带代码、文档、工具和 JSON 索引**：卡面、界面素材、字体、养成图片与语音都在
`assets/` 下，由你自己用 `tools/` 里的接入工具补齐，见 [素材指南](ASSETS.md)。

没有任何素材时插件也能正常加载并响应全部命令，只是输出形式不同：

| 内容 | 有素材 | 没素材 |
| --- | --- | --- |
| 帮助、规则、卡池、卡册、任务列表、好感信息 | 分页图片卡片 | 插件自绘背景的分页图片卡片 |
| 抽卡结果 | 卡面结果图 + 揭示图 | 带稀有度、卡号与卡名的文字清单 |
| `/卡图`、卡牌揭示图 | 原图 / 揭示图 | 文字提示“卡面未接入” |
| 好感页、奖励页、解花对照图、任务卡 | 原作版式图片 | 文字卡片或文字回执 |
| 语音 | 发送音频 | 返回提示文字，其他养成结果不受影响 |

系统里没有任何可用中日文字体时，图片渲染会整体关闭，常规回复退化为纯文字；曲目预览的版本选择必须发图片，缺少中文字体时会报错。
补齐素材后重新加载插件即可生效，不需要改配置。

替换文件后，在 MaiBot 插件管理中重新加载插件，或重启实例，确认日志显示加载成功。

### 1.3.3 → 1.3.4 补充说明

备份数据库后替换代码并重启插件。首次打开数据库会自动增加谱面类型并迁移终极完成记录：历史任务中的 `MASTER DX` 等标签恢复为 DX，无 DX 后缀的已知难度恢复为标准；已清理历史、无法恢复类型的记录保留跨类型去重，避免重复发奖。无需清空数据库或重新配置奖励。

终极任务改用 `/终极提交 <ID>`，管理员使用 `/终极审核 <ID> 通过|拒绝 [备注]`。原 `/终极完成 <用户> <ID>` 仍可用。驳回后可补图重提；若要更换终极任务，由管理员 `/任务重置 <ID>`。任务卡中的“起（按评级）”表示 S 档点数，完整奖励见任务规则。

谱面范围以配置的数据源为准；默认舞萌和中二接口仍带版本筛选，本次未扩大到其他地区或版本。舞萌字段定义见[落雪 API 文档](https://maimai.lxns.net/docs/api/maimai)。

### 2.2 从 1.3.0 / 1.3.1 / 1.3.2 升级到 1.3.4

1. 停止插件后，备份插件数据目录中的 `ongeki_gacha.db` 和实例的 `config.toml`。
2. 替换为 1.3.4 的代码与 JSON 索引，保留自己的配置、数据库和素材目录。
3. **同步奖励配置**：新增键缺省时使用新默认值，已有键仍保留旧值。只改版本号不会迁移奖励数值。
   要采用本版平衡方案，请在现有 `[plugin]` / `[growth]` 段中更新或补齐以下字段，
   不要重复追加同名段；其他自定义配置按需保留。

```toml
[plugin]
config_version = "1.3.4"

[growth]
monthly_event_days = 10
monthly_event_small_gift_days = [1, 3, 6, 9]
monthly_event_medium_gift_days = [4, 8]
monthly_event_large_gift_days = [5, 10]
monthly_event_bloom_ticket_days = [1]
monthly_event_fragments = 2
task_small_gifts_daily_cap = 3
task_small_gift_sources = ["normal"]
task_medium_gifts_daily_cap = 3
task_medium_gift_sources = ["challenge", "advanced"]
large_gift_fragment_price = 12
task_fragments_normal = 0
task_fragments_challenge = 1
task_fragments_advanced = 1
task_fragments_ultimate = 0
task_fragments_daily_cap = 1
ultimate_large_gifts_lifetime_cap = 0
large_gift_monthly_first_cap = 10
large_gift_second_price = 24
large_gift_monthly_second_cap = 20
large_gift_final_price = 60
gift_purchase_small_price = 20
gift_purchase_small_daily_cap = 10
gift_purchase_medium_price = 60
gift_purchase_medium_daily_cap = 5
challenge_bloom_ticket_min_level = 10.0
challenge_bloom_ticket_grade = "SSS"
challenge_bloom_ticket_cooldown_days = 30
```

4. 重新加载后查看 `/规则` 和 `/礼物`，确认活动为每月 1–10 日、大礼物月度三档价格为 12/24/60 碎片，
   挑战与高级挑战中礼物每日合计上限为 3。

已有点数、好感、物品和领取记录保留，不追扣库存，也不补发过去的签到或已审核任务。
月初活动按 UTC 日历日期发放；升级前已签到的当天不会再次领奖。
待审核任务按审核时的配置发奖，已用的每日任务礼物和碎片额度继续累计。
重复卡采用新碎片倍率，旧版本迁移补偿仍使用原倍率。
从 1.3.0 升级时，启动会补齐待发送揭示记录的解花状态列，原有记录保留。

### 2.3 从 1.2.1 升级

1. **备份**：复制插件数据目录中的 `ongeki_gacha.db`，并备份插件目录里的 `config.toml`。
2. **替换代码**：用 1.3.4 的插件目录覆盖旧目录，保留实例自己的 `config.toml`。
3. **加载**：在 MaiBot 插件管理中重新加载该插件，或重启实例。
4. **核对配置**：旧配置文件里缺少的新键会按默认值在运行时生效，已有键的值
   （例如管理员列表、卡池模式、奖励数值）保持不变。如果希望把新键写进 `config.toml`
   方便手工编辑，在插件配置页保存一次即可。奖励配置同时按上节同步。
5. **验证**：依次发送 `/帮助`、`/点数`、`/卡池`、`/好感 列表`、`/任务列表`，
   确认图片与文字回执正常；需要迁移的旧库还应核对迁移备份已生成。

升级会在同一数据库上就地迁移，先备份再写入；用户已有的资产不会被回收。
迁移执行前会生成一致性数据库备份（`before-growth-*.db` 等）到插件数据目录。

| 迁移项 | 触发条件 | 结果 |
| --- | --- | --- |
| 好感曲线 v1 → v2 | 存在 `affection-v1` 好感点 | 好感点 ×10，等级保持不变，只执行一次 |
| 养成库存初始化 | 首次启用养成且旧库非空 | 保留旧解花阶段，旧成长溢出的冗余卡折算为花之碎片 |
| 终极完成记录 | 存在旧的整曲完成记录 | 优先恢复具体难度，恢复不到时保留整曲锁 |
| 语音限流状态 | 旧的单调时间戳 | 视为过期清理，短期限流重新累计，资产不受影响 |

注意：待审核任务在审核时按**当时的配置**计奖，升级后调整奖励配置会影响尚未审核的任务。

## 3. 命令总览

### 3.1 用户命令

| 命令 | 说明 |
| --- | --- |
| `/签到` | 每日签到，结算基础点数、连续签到、7 天 / 15 天周期、月卡加成、每月活动物品与囤点奖励 |
| `/点数` | 查看点数、花之碎片、解花券、连续签到与月卡状态 |
| `/月卡` | 查看月卡状态；`/月卡 购买`、`/月卡 续费` 购买或续费 |
| `/抽卡 [常驻\|普通\|常规\|<池ID>] [1\|5\|11]` | 抽卡；不写数量时默认 1 连 |
| `/概率` | 查看当前稀有度权重、消耗与保底规则 |
| `/卡池 [列表\|下一期]` | 查看启用中的卡池、UP 角色、天井与轮替进度，并发送官方活动图 |
| `/天井` | 查看默认卡池的天井进度；满额后 `/天井 <卡ID>` 兑换选择卡 |
| `/天井列表` | 列出全部启用池的天井进度与选择卡 |
| `/天井池 <池ID> <卡ID>` | 指定某个启用中的卡池兑换天井选择卡 |
| `/卡册` | 卡册总览：17 名主角色加“其他”的持有进度 |
| `/卡册 <角色姓名> [页码]` | 查看某角色的一页卡册（卡 ID、稀有度、持有数量），翻页由用户指定 |
| `/卡图 <卡ID>` | 发送已拥有卡牌的高清原图 |
| `/好感 [角色姓名\|列表]` | 打开好感页，或列出 17 名角色的好感等级 |
| `/好感 <角色姓名> 卡面 <卡ID>` | 把已持有的该角色卡设为好感页立绘 |
| `/好感奖励 [角色姓名]` | 分页浏览该角色的 32 项奖励 |
| `/伙伴 <角色姓名>` | 选择当前伙伴 |
| `/陪伴` | 每日一次，提升伙伴好感 |
| `/送礼 <角色姓名> 小\|中\|大 [数量]` | 消耗礼物提升好感 |
| `/礼物` | 查看礼物、花之碎片、解花券数量、今日可购买额度和大礼物月度阶梯余额 |
| `/礼物 购买 小\|中 [数量]` | 用点数购买小/中礼物 |
| `/礼物 兑换 大 [数量]` | 每月前10份各12碎片、接着20份各24碎片，之后各60碎片不限量；跨档分段计价 |
| `/养成 <卡ID>` | 查看单卡养成状态、解花条件与消耗 |
| `/解花 <卡ID>` / `/超解花 <卡ID>` | 执行解花 / 超解花 |
| `/装扮` | 查看已解锁的称号与装饰 |
| `/装扮 称号\|装饰 <ID>` / `/装扮 称号\|装饰 卸下` | 装备或卸下装扮 |
| `/角色语音 [角色姓名] [序号]` | 查看语音列表或点播已解锁的档案语音 |
| `/角色语音 分类` | 说明自动回应与手动播放的区别 |
| `/接任务 普通\|挑战\|高级挑战\|终极 [音击\|舞萌\|中二]` | 接取随机任务，不指定游戏时三游戏全随机 |
| `/任务列表` | 查看自己的任务、状态与当天剩余次数 |
| `/任务完成 <任务ID>` | 同一条消息附成绩图，提交普通 / 挑战 / 高级挑战任务 |
| `/终极提交 <任务ID>` | 同一条消息附成绩图，提交终极任务 |
| `/规则` | 以图片卡片查看完整规则 |
| `/帮助` | 精简命令列表 |

### 3.2 管理员命令

管理员需要在 `[admin] admin_ids` 中配置 QQ 号。

| 命令 | 说明 |
| --- | --- |
| `/奖励 <@用户\|QQ号> <点数> [备注]` | 向指定用户发放点数，写入审计日志 |
| `/任务审核 <任务ID> <普通\|S\|SS\|SSS\|SSS+\|拒绝> [备注]` | 审核普通 / 挑战 / 高级挑战任务并发奖 |
| `/任务审核列表` | 查看全部待审核任务 |
| `/终极审核 <任务ID> <通过\|拒绝> [备注]` | 审核终极任务；通过须确认指定谱面达到 SSS+，拒绝后玩家可重提 |
| `/终极完成 <用户> <任务ID> [备注]` | 兼容旧入口，确认已提交的终极任务并发奖 |
| `/任务重置 <任务ID> [备注]` | 重置任务并返还当日次数 |
| `/任务清理 [天数]` | 立即清理已结束任务历史，待审核任务始终保留 |

已移除 `/og*`、`/打卡`、`/图鉴`、`/池子`、`/角色`、`/档案`、`/资料`、`/播放语音`、`/语音分类`
等重复写法，每个功能只保留一个标准命令。

## 4. 抽卡、卡池与天井

- 消耗：1 连 50 点、5 连 250 点、11 连 500 点。
- 抽取顺序：先按稀有度权重（默认 R 77 / SR 20 / SSR 3），再在池内按卡权重抽取，UP 卡按 `pickup_multiplier`（默认 ×10）加权。
- 保底：11 连必得 SR 或以上；5 连每用户每周首次触发一次 SR 或以上保底，每周四 00:00 按 `economy.tz_offset_hours` 配置时区重置。
- 月卡附带两次半价五连，使用后 5 连按 125 点结算。

### 4.1 卡池模式

- `rotation_mode = "cycle"`（默认）：按 `rotation_interval_days`（默认 15 天）循环使用已收录的历史官方卡池。
  `rotation_epoch` 可固定起点，留空时使用数据库首次记录，重启不会退回第一期。
- `rotation_mode = "official"`：按「几月几日」匹配历史官方池（忽略年份），可能同时启用多个池；
  `/卡池` 列出全部启用池，默认抽取最近开启的一个，也可用 `/抽卡 <池ID> <1/5/11>` 指定。
  当天没有活动池时使用常驻池。
- **常驻池**包含当前版本已有的全部 R/SR/SSR 基础卡，用 `/抽卡 常驻 11` 单独抽取。
- 活动池候选是“当期版本已有的全部 R/SR/SSR”再叠加本期 UP 与天井选择卡，是本插件采用的模拟模型，不代表官方完整实现。
  官方公告没有标注 UP 时会显示 `UP 卡：0 张`，但候选卡依然存在。
- 排表里的 `non_gacha_pool` 卡不进入任何抽卡池，改为签到按概率掉落。

### 4.2 天井

- 每抽到一张卡，当前卡池天井 +1 点；达到该池上限后可用 `/天井 <卡ID>` 兑换默认池的选择卡，
  或用 `/天井池 <池ID> <卡ID>` 指定池兑换。
- 每个卡池只能兑换一次，兑换后该池天井点清零；池子轮换后旧池天井不带入新池。
- `/天井列表` 可查看全部启用池的进度与可选卡。

### 4.3 数据的边界

排表来自 SEGA 官方 CARDMAKER 公告与 Artemis 的静态卡池数据，当前收录 63 个历史公告池
（2020-10 ～ 2026-07）。2023～2026 年仍有大量日常池未收录，official 模式只复现已收录池的当年同期排期，
不代表完整官方时间线。

抽卡回执中的“池内 UP 卡共 N 种”表示卡池设置，不是本次抽中的数量；“首次获得”才是本次新卡数。
五连会分别提示本次是否使用周保底，十一连会提示本次保底；消耗和余额单独列出。

## 5. 点数经济

| 项目 | 默认值 |
| --- | --- |
| 每日基础签到 | 70～110 点（平均约 90） |
| 连续签到 | 第 2 天起每天额外 +10，封顶 +100；断签重新从第 1 天算 |
| 连续 7 天奖励 | +600 点 |
| 连续 15 天周期奖励 | +1200 点（对应一次卡池轮替） |
| 月卡 | 1000 点 / 30 天，有效期每日签到额外 +80 点 |
| 月卡附赠 | 2 次半价五连；剩余不超过 3 天可续费 |
| 囤点档位 | 1000 / 3000 / 10000 点各触发 100 / 500 / 2000 点，每档一次，每 60 天重置 |
| 签到彩蛋卡 | 5% 概率掉落一张非抽卡收藏卡，并发送卡面 |

签到时还有极低概率的额外点数彩蛋（合计约十万分之一），属于彩蛋性质，不影响平衡计算。

月卡满勤 30 天共 2400 点，加两次半价五连省下的 250 点，合计 2650 点，扣除售价净 +1650 点；
漏签到会等比减少收益。囤点奖励一个周期最多 2600 点。

## 6. 随机任务

任务曲库来自音击、舞萌 DX、中二节奏三款游戏，不指定游戏时混合随机，也可以用
`/接任务 挑战 中二` 这样的写法限定游戏。
音击 LU 中具有有效内部定数的计分谱可参与任务；零定数及明确不计分的特殊谱继续排除。

| 类型 | 每日次数 | 选谱规则 | 奖励 |
| --- | --- | --- | --- |
| 普通 | 3 | 全量曲库随机，任意难度 | 35 点 |
| 挑战 | 2 | 至少有一张定数 ≥10 的谱面，取该曲**最低**达标谱面 | S 60 / SS 80 / SSS·SSS+ 100 点 |
| 高级挑战 | 1 | 至少有一张定数 >= 12.7 谱面，取该曲最低达标谱面 | S 105 / SS 115 / SSS·SSS+ 125 点 |
| 终极 | 一次一个 | 谱面定数 >= 14.7（内部定数，不是“14 级+”），要求 SSS+ | 30000 点 |

- 挑战与高级挑战要求指定谱面或同类型更高难度达到 S 及以上；舞萌标准与 DX 不互相替代，音击 LU 单独处理。
- 终极任务仅限指定谱面达到 SSS+，完成记录按“游戏＋曲目＋谱面类型＋难度”保存；舞萌标准与 DX 独立，已完成谱面不再重复。
- 提交任务必须附成绩照片，由管理员在同群人工审核；普通任务使用“普通”档审核，挑战与高级挑战使用 S/SS/SSS/SSS+。
- 普通 / 挑战 / 高级挑战未完成任务在每日 00:00（UTC）自动过期；待审核任务与可重提的终极任务保留。
- 已通过、已过期、已重置的记录默认保留 30 天，之后自动清理；`/任务清理 [天数]` 可立即清理。
- 加入高级挑战后，普通 + 挑战 + 高级挑战的每日点数上限为 430 点。

## 7. 主角色好感养成

### 7.1 好感与伙伴

- 17 名主角色默认全部开放，不需要先抽到角色卡。
- `/伙伴 <角色姓名>` 选择伙伴；`/陪伴` 每个账号每天一次，+300 好感，按 UTC 日期结算，换群不重置。
- `/送礼 <角色姓名> 小|中|大 [数量]`：小礼物 300、中礼物 1000、大礼物 10000 好感。
- `/好感 [角色姓名|列表]` 打开好感页或 17 人总览，显示等级、累计好感、基础资料与资料解锁。
- 好感无上限：奖励节点到 Lv1000（累计 4,455,000），之后按最高档每级 15,600 继续累计，
  心形最多显示 99/99（Lv9999）。Lv50 = 45,000、Lv100 = 165,000、Lv200 = 363,000。

### 7.2 基础 N 卡

- 每名角色的第一张基础 N 卡在**首次查看该角色好感页**时获得，并发送卡牌揭示图。
- 之后 10 张由好感节点发放，累计到 11 星满星。
- 这 17 张基础 N 卡从抽卡池、签到随机掉落与天井候选中排除，其他活动 N 卡来源不变。
- 无论首获还是节点发放都不折算花之碎片。

### 7.3 奖励与装扮

- 每名角色 32 项固定奖励：10 条档案语音、9 个称号、10 张基础 N 卡、2 个名牌、1 个装饰；
  到达等级自动领取，`/好感奖励 [角色姓名]` 分页浏览。
- `/装扮` 查看已解锁称号与装饰；`/装扮 称号|装饰 <ID>` 装备，`/装扮 称号|装饰 卸下` 卸下。
- 新获得的称号与装饰自动装备，同类型更高节点覆盖为最新一件；不同类型可自由混搭。
- 名牌停止展示，只作收藏保留。

### 7.4 礼物与物品

| 物品 | 默认获取方式 | 限制 |
| --- | --- | --- |
| 小礼物（300 好感） | 每月活动第 1、3、6、9 日各 1 份；普通任务审核每次 1 份；点数购买 | 活动合计 4 份；任务每日最多 3 份；每日最多购买 10 份，20 点/份 |
| 中礼物（1000 好感） | 每月活动第 4、8 日各 1 份；挑战/高级挑战审核每次 1 份；点数购买 | 活动合计 2 份；任务每日合计最多 3 份；每日最多购买 5 份，60 点/份 |
| 大礼物（10000 好感） | 每月活动第 5、10 日各 1 份；碎片兑换 | 活动合计 2 份；月度三档12/24/60碎片，额度10/20/不限量；任务不发大礼物 |
| 花之碎片 | 重复卡折算；每月活动第 2、7 日各 2；挑战/高级挑战审核每次 1、每日合计上限 1 | 普通与终极任务不发；超解花每次 90，兑换大礼物按月度12/24/60阶梯价 |
| 解花券 | 每月第 1 日签到 1 张；挑战目标定数 >= 10、高级挑战目标定数 >= 13.5 且 SSS/SSS+，各可获 1 张 | 高级挑战冷却15天；挑战目标定数>=10且SSS/SSS+另得1张、独立冷却30天（一个月按30天计）；签到、挑战、高级挑战互不占用冷却；解花消耗 1 张 |

- 每月 1–10 日开放一轮签到活动，按日历日期发放；漏签不补发，下月重新开始，11 日以后不发活动物品。原有签到点数、连续签到奖励及随机卡掉落照常结算。
- 重复卡碎片（满星内 / 满星后）：N、R 为 1 / 1，SR 为 2 / 3，SR+ 为 2 / 4，SSR 为 4 / 8；首获和好感奖励 N 卡不产生碎片。
- 从月初起连续 30 天签到且每天完成至少 1 次挑战或高级挑战审核，固定碎片为活动 4 + 任务 30 = 34，重复卡另计；新规则只影响之后的发放，已有库存与旧版迁移补偿保留。
- 本月尚未兑换时，`/礼物 兑换 大 3` 消耗 36 片碎片；若本月已兑 9 份，同一指令跨档消耗 12 + 24 × 2 = 60 片，均获得 3 份大礼物；兑换与库存同事务结算，同消息重投不重复扣减。兑换后用 `/送礼 <角色姓名> 大 [数量]` 提升好感，`/礼物` 可查看兑换价格与可换数量。
- 任务礼物和碎片额度按审核成功的 UTC 日期累计，跨群共享；不是接取或提交任务的日期。
- 小中礼物每天00:00重置，大礼物每月1日00:00重置阶梯额度，均使用经济配置时区、按账号跨群共享；仅最高价档不限量。

### 7.5 解花与超解花

- **解花**：需要角色好感 Lv100 并消耗 1 张解花券，**不要求卡牌满星**。
- **超解花**：需要已解花、角色好感 Lv200、卡牌满星（N 卡 11 星、其他 5 星），消耗 90 花之碎片。
- 这里的等级指角色好感等级，与卡面等级无关。
- 成功后发送阶段对照图；`/养成 <卡ID>` 可查看当前阶段与下一步条件。
- 归属未确认的卡暂不开放新解花，旧阶段继续继承。

### 7.6 角色语音

- **自动回应**：小礼物、中/大礼物、好感升级后自动发送一条（每角色 3 条，升级优先，每笔最多一条）。
- **手动播放**：每个角色 10 条档案语音，按好感等级解锁，用 `/角色语音 <角色姓名> <序号>` 点播。
- 两类语音共用冷却与每分钟限流（默认 10 秒冷却、每分钟 5 次）；冷却次数、请求占位与每分钟计数写入数据库，
  重启不清零。
- 发送结果不确定时不会自动重试，也不回滚养成结果；可稍后主动重新点播。

## 8. 显示与回退

- 长内容（规则、卡池、卡册、任务列表、好感奖励等）排版为分页图片卡片，标注页码；
  短回复（回执、报错、单条状态）直接发文字。
- 图片渲染或发送失败时自动回退为纯文本，不影响指令结果。
- 一次性渲染图片在运行目录中按 `[ui] render_cache_ttl_seconds`（默认 24 小时）清理；
  卡池公告图与曲绘缓存按各自的 TTL 保留。
- 签到、任务、周保底与卡池轮替均按国际时间 UTC 计算。

## 9. 数据目录与素材

默认卡牌素材与索引目录（相对插件目录，与玩家数据库目录不同）：

```text
assets/card_data/
```

- `card_info_merged.json`：卡牌元数据。
- `card_data_manifest.json`：卡面、卡牌 JSON 与 `gacha_pools.json` 的大小和 SHA-256 校验清单。
- `gacha_pools.json`：官方 CARDMAKER 卡池排表。
- `ui_card_*.png`：卡面素材，不随代码仓库与发布包分发，需要自己接入。

用 `assets.cards_dir` 与 `assets.card_info_json` 可切换到其他目录。
素材的来源与接入步骤见 [素材指南](ASSETS.md)。

常用校验命令（在插件父目录运行）：

```powershell
python ongeki_gacha/tools/sync_card_data.py --check      # 卡面完整性与哈希
python -m ongeki_gacha.tools.verify_growth_install       # 养成素材与语音逐项散列
python -m ongeki_gacha.tools.verify_text_fallback        # 无素材兜底
```

## 10. 配置项

配置由 `config_model.py` 定义，Runner 首次加载时生成 `config.toml`，
通过插件配置页保存后触发热更新，失败时会写日志；手动改文件后建议重新加载并核对 `/规则`。
以下是 1.3.4 的默认值。

升级时旧配置文件缺少的新键会按默认值在运行时生效；如果想把这些键写进文件方便手改，
在插件配置页保存一次即可。已有键的值不会被覆盖。

```toml
[plugin]
enabled = true
config_version = "1.3.4"

[assets]
cards_dir = "assets/card_data"
card_info_json = "assets/card_data/card_info_merged.json"

[pool]
weight_n = 0
weight_r = 77
weight_sr = 20
weight_sr_plus = 0
weight_ssr = 3
schedule_json = "assets/card_data/gacha_pools.json"
rotation_mode = "cycle"
rotation_interval_days = 15
rotation_epoch = ""
pickup_multiplier = 10
strict_pool_cards = true

[economy]
cost_1 = 50
cost_5 = 250
cost_11 = 500
min_reward = 70
max_reward = 110
tz_offset_hours = 0
streak_daily_step = 10
streak_daily_max = 100
streak_weekly_reward = 600
streak_cycle_days = 15
streak_cycle_reward = 1200
non_gacha_checkin_probability = 0.05
savings_threshold_1 = 1000
savings_bonus_1 = 100
savings_threshold_2 = 3000
savings_bonus_2 = 500
savings_threshold_3 = 10000
savings_bonus_3 = 2000
savings_bonus_reset_days = 60

[monthly_card]
price = 1000
duration_days = 30
daily_bonus = 80
renew_max_remaining_days = 3
half_price_5_pull_count = 2

[admin]
admin_ids = []
allow_local_operator = false

[task]
enabled = true
normal_count = 3
challenge_count = 2
advanced_count = 1
challenge_min_level = 10.0
advanced_min_level = 12.7
ultimate_min_level = 14.7
normal_reward = 35
challenge_reward_s = 60
challenge_reward_ss = 80
challenge_reward_sss = 100
advanced_reward_s = 105
advanced_reward_ss = 115
advanced_reward_sss = 125
ultimate_reward = 30000
require_photo = true
auto_reset = true
auto_cleanup_history = true
task_history_retention_days = 30
catalog_cache_ttl = 3600
ongeki_source_url = "https://dp4p6x0xfi5o9.cloudfront.net/ongeki"
maimai_song_url = "https://maimai.lxns.net/api/v0/maimai/song/list?version=25500&notes=false"
chunithm_song_url = "https://maimai.lxns.net/api/v0/chunithm/song/list?version=23000&notes=false"
maimai_asset_url = "https://assets2.lxns.net/maimai"
chunithm_asset_url = "https://assets2.lxns.net/chunithm"

[growth]
enabled = true
voice_enabled = true
automatic_voice_enabled = true
companion_points = 300
gift_small_points = 300
gift_medium_points = 1000
gift_large_points = 10000
monthly_event_days = 10
monthly_event_small_gift_days = [1, 3, 6, 9]
monthly_event_medium_gift_days = [4, 8]
monthly_event_large_gift_days = [5, 10]
monthly_event_bloom_ticket_days = [1]
monthly_event_fragments = 2
task_small_gifts_daily_cap = 3
task_small_gift_sources = ["normal"]
task_medium_gifts_daily_cap = 3
task_medium_gift_sources = ["challenge", "advanced"]
large_gift_fragment_price = 12
task_fragments_normal = 0
task_fragments_challenge = 1
task_fragments_advanced = 1
task_fragments_ultimate = 0
task_fragments_daily_cap = 1
gift_purchase_small_price = 20
gift_purchase_medium_price = 60
bloom_items = ["bloom_ticket", "flower_fragment"]
bloom_levels = [100, 200]
bloom_costs = [1, 90]
bloom_ticket_source_kind = "advanced"
bloom_ticket_source_min_level = 13.5
bloom_ticket_source_grade = "SSS"
bloom_ticket_cooldown_days = 15
ultimate_large_gifts_lifetime_cap = 0
voice_cooldown_seconds = 10
voice_minute_limit = 5
voice_request_ttl_seconds = 600
voice_inflight_limit = 10
voice_send_timeout_seconds = 30
rules_overrides = {}
large_gift_monthly_first_cap = 10
large_gift_second_price = 24
large_gift_monthly_second_cap = 20
large_gift_final_price = 60
gift_purchase_small_daily_cap = 10
gift_purchase_medium_daily_cap = 5
challenge_bloom_ticket_min_level = 10.0
challenge_bloom_ticket_grade = "SSS"
challenge_bloom_ticket_cooldown_days = 30

[ui]
render_cache_ttl_seconds = 86400
pool_image_cache_ttl_seconds = 604800
max_pool_image_bytes = 8388608
short_reply_max_lines = 10
short_reply_max_chars = 700

[preview]
enabled = true
automatic_ultimate = true
providers = ["netease_api", "netease", "meting_tencent", "meting_kugou", "meting_kuwo", "meting_netease", "deezer"]
netease_api_url = ""
netease_cookie = ""
meting_api_url = ""
allow_preview_fallback = false
max_duration_seconds = 1200
ffmpeg_path = ""
request_timeout_seconds = 15
download_timeout_seconds = 60
total_timeout_seconds = 180
send_timeout_seconds = 90
cooldown_seconds = 10
cache_ttl_seconds = 86400
max_download_bytes = 67108864
```

要点：

- `voice_enabled = false` 会保留完整的养成功能但停止发送语音；`automatic_voice_enabled` 只控制自动回应。
- `rules_overrides` 按顶层键覆盖 `assets/growth/rules_draft.json` 的规则，具名字段优先。
- 养成默认规则为 `growth-balance-v10`；旧配置中的具名数值不会自动覆盖，升级时请按上例同步月初活动、任务礼物和碎片字段。活动日期必须在 1 到 `monthly_event_days` 之间；小、中、大礼物日期不可重复或重叠。`monthly_event_bloom_ticket_days` 可与礼物日重叠，每个指定日发 1 张券；无礼物或券的活动日按 `monthly_event_fragments` 发碎片。
- `task_small_gift_sources` / `task_medium_gift_sources` 指定每次审核发 1 份小/中礼物的任务类型，分别受每日上限控制。`large_gift_fragment_price` 为第一档价格，后续档位由 `large_gift_second_price` / `large_gift_final_price` 配置，三档价格必须严格递增。
- `strict_pool_cards = true` 时严格按池子候选卡抽取，不会混入当期尚未出现的未来版本卡。
- `[task]` 中的数据源地址可换成镜像；`catalog_cache_ttl` 控制曲库缓存秒数。


1.3.4 礼物商店配置：`gift_purchase_small_price` / `gift_purchase_small_daily_cap` 默认20/10，
`gift_purchase_medium_price` / `gift_purchase_medium_daily_cap` 默认60/5。
大礼物第一档额度 `large_gift_monthly_first_cap` 默认10；第二档单价/额度
`large_gift_second_price` / `large_gift_monthly_second_cap` 默认24/20；第三档 `large_gift_final_price` 默认60，不限量。
旧 `gift_purchase_*_weekly_cap` 不再使用，升级时可移除；升级前的大礼物兑换不追计月度额度，不追扣碎片。
挑战解花券由 `challenge_bloom_ticket_min_level` / `challenge_bloom_ticket_grade` / `challenge_bloom_ticket_cooldown_days`
控制，默认10/SSS/30；高级挑战原有15天冷却独立保留。
音击 LU 的有效内部定数计分谱参与任务，零定数谱继续排除；旧曲库缓存首次使用时自动尝试刷新。

## 11. 故障排查

- **加载失败并提示数据不可用**：检查 `assets.cards_dir`、`assets.card_info_json` 与日志中的文件路径；
  如果缺少随包 JSON 索引，重新解压同版本插件包补齐。只缺卡面不影响加载，插件会改用文字输出。
- **`/卡池` 显示空排表**：确认 `assets/card_data/gacha_pools.json` 存在，必要时重新获取插件包。
- **`/卡池` 显示 `UP 卡：0 张`**：官方公告未提供 UP 名单时属于正常；可用 `/天井列表` 查看选择卡，
  用 `/概率` 查看实际权重。
- **`/卡图` 提示未拥有**：该命令只发送当前用户已获得的卡。
- **`/卡图` 提示“卡面未接入”**：该卡 PNG 还没导入，按 [素材指南](ASSETS.md) 补齐。
- **抽卡只返回文字清单**：卡面、界面素材或字体至少缺一样，看插件日志里的提示行。
- **好感页只有文字**：`assets/growth/images/` 还没接入。
- **`/签到` 提示重复**：同一配置时区日期只能签到一次。
- **语音点播提示限流**：默认 10 秒冷却、每分钟 5 次，可在 `[growth]` 调整。
- **抽卡提示数量错误**：数量只能是 1、5 或 11。

## 12. 已知限制

- **卡池排表不完整**：只收录 63 个历史公告池（2020-10 ～ 2026-07），
  2023～2026 年仍有大量日常池缺失；official 模式只复现已收录池的当年同期排期，
  不代表完整官方时间线。当天没有活动池时使用常驻池。
- **57 张卡的角色归属未确认**：这些卡只保留收藏与旧解花阶段，不开放新的解花与超解花。
- **通知是“至少一次投递”**：若平台已收到图片、而插件进程恰好在数据库确认前退出，
  之后可能再发一次同样内容；资产结算与通知分离，不会重复发卡。
- **角色语音与卡牌图片依赖本地素材**：缺失时对应输出退化为文字；曲目预览从音源及游戏曲库获取音频和曲绘，失败会报错。
- **离线回归不等于线上验收**：本项目只做本地模拟，未在真实长周期运行中验证送达与并发表现。

更多责任范围与限制见 [免责声明](DISCLAIMER.md)。
