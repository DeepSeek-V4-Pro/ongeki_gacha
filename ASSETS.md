# 素材指南（来源、接入与校验）

本插件只负责本地模拟、展示和抽卡逻辑。**卡面 PNG、角色图层、UI 素材、字体、养成图片、
语音与卡牌元数据均不属于插件代码的 LICENSE 范围**，其权利归 SEGA、相关角色或素材权利人所有。

发布包**只包含代码、文档、工具和 JSON 索引**，不含任何素材：
卡面、界面素材、字体、养成图片与语音都需要你自己准备并接入。
本文讲两块内容：素材可以从哪里查、怎么接进本地 `assets/`。
责任范围与使用限制见 [免责声明](DISCLAIMER.md) 与 [第三方声明](NOTICE.md)。

不随包附带素材的原因：

- 版权归属不随插件代码授权；
- 发布包体积过大；
- 不同用户对素材使用范围和来源的授权情况不同；
- 公开渠道中的成品图、缩略图与本地标准卡面（768×1052）并不等同。

所有接入工具都是离线脚本：它们不下载、不解包、不调用网络，只处理你本地的文件。

## 1. 一分钟概览

| 素材类别 | 目标位置 | 接入方式 | 没有它时 |
| --- | --- | --- | --- |
| 卡面 | `assets/card_data/ui_card_<6位ID>.png` | `tools/card_asset_tools.py` 扫描 / 合成 / 导入 | 抽卡结果图、`/卡图`、卡牌揭示图改为文字 |
| 界面素材 | `assets/ui/*.webp`、`assets/ui/*.ttf` | 手动复制 | 抽卡结果图与揭示图改为文字 |
| 字体 | `assets/fonts/NotoSansCJKsc-*.otf` | 手动复制 | 回退系统字体；系统也没有时全部改纯文字 |
| 养成图片 | `assets/growth/images/**`、`assets/growth/rewards/**` | `tools/build_growth_assets.py` | 好感页、奖励页、解花对照图改为文字卡片 |
| 语音 | `assets/growth/voices/**` | `tools/build_affection_voice_assets.py` 等 | 语音命令返回提示文字，其他功能正常 |

## 2. 没有素材时会怎样（文字兜底）

插件在完全没有素材的情况下也能加载并响应全部命令：

- **卡片类内容照常出图**：帮助、规则、卡池、卡册、任务、好感等长文本，使用插件自绘的浅色渐变背景
  加文字渲染成分页图片；这条路径不读取任何游戏素材。
- **需要游戏素材的图片改为文字**：抽卡结果图换成带卡名与 ID 的文字清单；卡牌揭示图、
  `/卡图`、好感页、奖励页、解花对照图、任务卡都会退化成文字或文字卡片。
- **连字体都没有时**：所有图片渲染关闭，全部回复走纯文字。
- 插件加载时会打印一条日志，说明缺哪类素材；补上素材后重新加载插件即可生效，不需要改配置。

想确认自己的包确实能这样跑：

```powershell
python -m ongeki_gacha.tools.verify_text_fallback
```

它会复制一份不含任何二进制素材的副本并实际执行一轮命令，输出 `"ok": true` 表示通过。

## 3. 目录约定

```text
assets/
├─ card_data/         卡面与卡牌索引
│  ├─ card_info_merged.json   卡牌索引（随包）
│  ├─ gacha_pools.json        卡池排表（随包）
│  └─ ui_card_<6位ID>.png     卡面（自己接入）
├─ ui/                抽卡结果图/揭示图用的界面素材（自己接入）
├─ fonts/             渲染字体（自己接入，可选）
└─ growth/
   ├─ *.json          养成目录、曲线、奖励表（随包）
   ├─ images/         好感页、解花、物品图素材（自己接入）
   ├─ rewards/        称号、名牌、装饰预览（自己接入）
   └─ voices/         档案语音与事件语音（自己接入）
```

不想把素材放进插件目录时，可以用 `--dest` 导入到别处，再改配置指向：

```toml
[assets]
cards_dir = "<你的素材目录>"
card_info_json = "<你的素材目录>/card_info_merged.json"
```

## 4. 素材来源

以下渠道只用于查询公开信息或核对素材，**不保证提供可直接分发的素材**；
公开仓库不代表你可以直接复制、分发其中涉及的受版权保护素材。

### 4.1 SEGA 官方渠道

#### 官方オンゲキ信息站

- 首页：<https://info-ongeki.sega.jp/>
- 官方发布文章：<https://info-ongeki.sega.jp/category/game/>
- CARDMAKER 卡池公告：<https://info-ongeki.sega.jp/category/cardmaker/>
- 官方活动/新曲/新卡页面通常包含公开的宣传图和卡面预览。

这个渠道适合：

- 确认某张卡是否真实存在；
- 获取卡名、稀有度、活动日期、卡池和官方宣传图；
- 核对 `card_info_merged.json` 中缺失的新卡 ID；
- 下载公告中的公开图片，作为“预览/对照图”，但不一定等同于最终卡面图层。

官方文章中的图片路径一般形如：

```text
https://info-ongeki.sega.jp/wp-content/uploads/YYYY/MM/<hash>.png
https://info-ongeki.sega.jp/wp-content/uploads/YYYY/MM/<hash>-219x300.png
```

你可以在页面源码或浏览器开发者工具的 Network / Images 面板中查找。
插件发布包不包含抓取脚本，也不提供自动抓取支持；
请自行确认权限后使用官方公开图片。

#### オンゲキ官方主站

- 官方主站：<https://ongeki.sega.jp/>
- 角色/插画页面：<https://ongeki.sega.jp/character/>
- 卡牌取扱说明：<https://ongeki.sega.jp/card/>

官方主站适合了解游戏背景、角色设定和官方公开插图，但“カードの取扱いについて”页面主要说明实体卡和取扱规则，不提供完整卡面下载库。

#### オンゲキ-NET（需用户自己的官方账号）

- 官方连动网站：<https://ongeki-net.com/ongeki-mobile/>

如果你拥有自己的 SEGA ID / Aime 账号，可以在允许的范围内登录官方在线服务，查看自己的游戏数据、活动信息、公告以及官方公开页面。插件不会替你登录、不会保存你的账号密码。

请自行遵守《利用規約》和账号安全要求。

### 4.2 公开数据库与社区资料

#### OTOGE DB

- 地址：<https://otoge-db.net/ongeki/>

OTOGE DB 是公开的曲目/谱面数据库，通常包含版本、曲名、难度等信息。它可以帮助你确认当前版本和新增曲目，但一般不是卡面图库。

#### AquaDX / AquaNet 公开 API

- 项目仓库：<https://github.com/MewoLab/AquaDX>
- 公开实例：<https://aquadx.net>

公开 API 可查询玩家排名、最近游玩记录和卡组 ID。例如：

```text
POST https://aquadx.net/aqua/api/v2/game/ongeki/user-summary
POST https://aquadx.net/aqua/api/v2/game/ongeki/recent
```

通过该接口可以发现本地尚未收录的卡 ID，但接口**不提供“卡名 + 卡面”**的完整静态卡表。它只能帮助你确认：

- 某 ID 是否真实出现在实机数据中；
- 出现日期、活动、曲目和卡组位置；
- 哪些 ID 仍缺少本地素材。

AquaDX 是社区项目，不是 SEGA 官方服务；使用时请自行确认访问权限、条款和素材用途。

#### Artemis / 公开静态数据仓库

- 仓库中的 `static_gachas.csv`、`static_gacha_cards.csv` 等公开数据可用于梳理卡池日期、UP、选择卡和权重。
- 仓库中的 `game_card.json`、`game_chara.json` 通常只包含元数据，不包含 PNG/WebP 卡面。

公开仓库只能用于查阅元数据和实现本地逻辑，不代表你可以直接复制、分发其中涉及的受版权保护素材。

### 4.3 本地已有素材

如果你已经持有合法来源的卡面或更新包，可以按下面的顺序检查，再决定怎么接入：

```powershell
# 检查插件默认素材目录
python tools/sync_card_data.py --check

# 改用自己的素材目录
python tools/sync_card_data.py `
  --source ./path/to/cards `
  --source-json ./path/to/card_info_merged.json `
  --check
```

如果目录中没有 `ui_card_*.png`，插件会提示“卡牌数据不可用”。
也可以在插件配置中修改：

```toml
[assets]
cards_dir = "./path/to/cards"
card_info_json = "./path/to/card_info_merged.json"
```

自行处理本地游戏包、更新包或解包中间文件时，请遵守：

- 不得绕过 DRM / 加密保护；
- 不得上传或公开分享受版权保护的原始素材包；
- 仅用于个人本地研究、娱乐或已获授权的用途；
- 发现无授权素材时应立即停止使用并联系权利人/维护者。

## 5. 卡面接入

### 5.1 先看本地有什么

```powershell
python tools/card_asset_tools.py scan --source .\素材目录
```

工具识别这几类文件名（`100001` 可以是任意 6 位卡牌 ID）：

```text
ui_card_100001.png                              成品卡面
UI_Card_Chara_100001_P.webp                     RinNET 角色 P 图层
ui_card_chara_100001_p.png                      解包后的角色 P 图层
ui_card_chara_100001.png                        普通角色图层
```

`icon`、`holo`、`mask` 等附属图层不会被误当作卡面。文件名随机时用 `--mapping` 指定映射：

```powershell
python tools/card_asset_tools.py scan `
  --source .\素材目录 `
  --mapping .\映射.csv `
  --report temp\scan.json
```

映射 CSV（`id,file`）或 JSON（`{"100001": "随便取的名字.png"}`）都可以。

### 5.2 只有角色图层时先合成

```powershell
python tools/card_asset_tools.py compose `
  --source .\角色图层 `
  --layers .\通用图层 `
  --info assets\card_data\card_info_merged.json `
  --out temp\card_art
```

输出是 768×1052 的近似净卡面：包含背景、边框、属性/稀有度/学年图标和近似卡名，
不含动态 HUD、holo、星级、解花标记和底部 ID 条（插件渲染时会自己叠加 ID 条）。
不需要卡名文字可加 `--skip-text`；只合成部分 ID 用 `--ids 100001,100002`。

想更接近上游排版时，可用浏览器版（需要 Playwright，脚本会自动使用本机 Edge/Chrome）：

```powershell
python tools/compose_card_art.py `
  --chara-dir .\角色图层 `
  --layers-dir .\通用图层 `
  --info assets\card_data\card_info_merged.json `
  --out temp\card_art_browser
```

### 5.3 导入插件目录

```powershell
python tools/card_asset_tools.py import `
  --source .\成品卡面 `
  --info assets\card_data\card_info_merged.json `
  --dest assets\card_data
```

- 默认会更新目标目录的 `imagePresent`，**分批导入不会丢掉之前接入的卡面**。
- 混合目录（既有成品也有图层）时加 `--layers .\通用图层`，成品优先、缺失才合成。
- 只导入部分卡用 `--ids 100001,100002,100003`。
- 只有一次性导入完整包时才考虑 `--keep-json`。

也可以手动按命名放入 `assets/card_data/`：

```text
ui_card_<6位ID>.png
```

例如 `ui_card_104490.png`。元数据中的 `imageFile` 会决定实际文件名；
默认命名规则为 `ui_card_<6位ID>.png`。

### 5.4 校验

```powershell
python tools/card_asset_tools.py verify
python tools/sync_card_data.py --check          # 完整哈希校验，较慢
python tools/sync_card_data.py --quick          # 只查文件是否存在
```

`sync_card_data.py` 还可以重建清单、从别的目录同步：

```powershell
python tools/sync_card_data.py --dry-run
python tools/sync_card_data.py --source .\cards --source-json .\card_info_merged.json
```

## 6. 界面素材

抽卡结果图的星级、MAX、解花印和数字字体放在 `assets/ui/`：

```text
UI_Card_star_00.webp / UI_Card_star_01.webp        星级
UI_Card_max_00.webp                                MAX 标记
UI_CMN_PrintMark_01_kaika.webp / ..._02_tyoukaika.webp   解花 / 超解花印
SEGA_Humming_v2-B.ttf                              结果图数字字体
```

把这几个文件按原名放进 `assets/ui/` 即可，没有命令行工具。
缺任意一个，抽卡结果图与揭示图都会改为文字输出（其他功能不受影响）。

## 7. 字体

把 Noto Sans CJK SC 的 Regular 与 Bold 放到 `assets/fonts/`：

```text
NotoSansCJKsc-Regular.otf
NotoSansCJKsc-Bold.otf
```

这两个文件按 SIL Open Font License 1.1 授权，`assets/fonts/LICENSE` 与 `sources.json`
记录了许可与摘要。没有这两个字体时插件会自动使用系统中文字体
（Windows 微软雅黑/黑体、Linux Noto、macOS 苹方）；系统也没有可用的中日文字体时，
全部回复退化为纯文字。

## 8. 养成图片

养成图片由本地提取结果打包而成，需要你自己准备提取目录（含
`asset_manifest.json`、`character_affection.json` 与官方立绘目录）：

```powershell
python -m ongeki_gacha.tools.build_growth_assets `
  --source <提取目录> --output assets\growth
python -m ongeki_gacha.tools.build_growth_theme
```

`build_growth_assets` 会把白名单素材复制到 `assets/growth/images/` 并写出
`visual_asset_manifest.json`（逐项记录来源、尺寸与 SHA-256）；
`build_growth_theme` 从角色立绘取色生成 `character_theme.json`，不需要参数。
缺少这些图片时，好感页、奖励页与解花对照图会自动退化为文字卡片。

## 9. 语音

语音来自你自己提取的语音目录，按目录里的 `voice_catalog.json` /
`event_voice_catalog.json` 打包：

```powershell
python -m ongeki_gacha.tools.build_affection_voice_assets `
  --source <提取目录>\voice_extracted --output assets\growth
python -m ongeki_gacha.tools.build_event_voice_assets `
  --source <提取目录>\event_voice_extracted --output assets\growth
```

打包后先做一次自动校验（解码、时长、响度、散列），确认无误再写入验收状态：

```powershell
python -m ongeki_gacha.tools.review_voice_assets --root . --report temp\voice_review.json
python -m ongeki_gacha.tools.review_voice_assets --root . --approve
```

只有 `listening_review == "verified"` 的语音才会发送；想先接入但不发语音，
把 `[growth] voice_enabled` 设为 `false` 即可。语音缺失时命令会返回提示文字，
送礼、养成等其他结果不受影响。

## 10. 接入完成后自检

```powershell
# 素材完整度（角色、卡牌、界面素材、语音的逐项散列）
python -m ongeki_gacha.tools.verify_growth_install

# 无素材兜底（复制一份不含二进制的副本实际跑一轮命令）
python -m ongeki_gacha.tools.verify_text_fallback

# 卡面接入结果
python tools/card_asset_tools.py verify
```

按这个顺序核对通常最省事：

1. 在官方信息站查询活动日期、卡名、稀有度和卡池公告。
2. 用官方宣传图确认卡面样式和活动主题。
3. 如果自己持有合法来源的本地包/更新包，扫描出对应 `Card.xml` 和角色图层。
4. 用 `tools/card_asset_tools.py scan` 检查本地素材是否齐全。
5. 用 `tools/card_asset_tools.py import` 接入成品，或先 `compose` 合成角色图层。
6. 用 `tools/sync_card_data.py --dry-run` 预览将同步多少张卡。
7. 确认来源和权限后运行 `tools/sync_card_data.py`。
8. 运行 `verify_growth_install` 与 `verify_text_fallback` 做最终校验。

## 11. 常见问题

- **为什么不随插件提供全部卡面？** 版权不属于项目代码，且公开渠道中的成品图、缩略图和本地标准图层
  并不总是等价；维护者不能替用户确认其获取、复制和使用素材的权限。
- **插件不加载，提示卡牌索引不可用**：先跑 `python tools/sync_card_data.py` 生成
  `assets/card_data/card_info_merged.json`；卡面可以之后再接。
- **已经有 `card_info_merged.json`，为什么还提示卡图缺失？** 元数据 JSON 不需要图片；
  显示 `/卡图` 和合成抽卡结果才需要 `ui_card_*.png`。请检查 `assets/card_data/`
  目录或 `assets.cards_dir` 配置。
- **抽卡只有文字清单**：说明卡面、界面素材或字体至少缺一样，看插件日志里的那行提示。
- **`/卡图` 提示“卡面未接入”**：该卡的 PNG 还没有导入，属正常提示。
- **好感页只有文字**：`assets/growth/images/` 还没接入，或该素材文件缺失。
- **语音提示素材校验未通过**：先跑 `review_voice_assets` 检查并 `--approve`。
- **官方宣传图可以直接用来做卡面吗？** 通常不建议。官方宣传图可能经过裁切、合成、缩小或加文字/边框，
  尺寸也不一定是 768×1052。它适合做预览、对照和确认，不应直接伪装成完整标准卡面。
- **我已确认有权限，应该放哪些文件？** 建议直接使用
  `tools/card_asset_tools.py import --source <素材目录>`，
  脚本会自动识别/重命名、转换尺寸、更新 `imagePresent` 并重建校验清单；
  也可以手动按 `ui_card_<6位ID>.png` 命名放入 `assets/card_data/`。

## 12. 权利与提醒

- 工具箱不下载网络资源、不扫描游戏包、不解密、不上传任何文件。
- 卡面、角色图层、UI、字体、语音等素材权利归 SEGA 或相应权利人所有，不属于项目代码授权范围。
- 本项目与 SEGA 无隶属、授权、合作或认可关系。
- 请在获取、复制、使用、分发素材前自行确认权利和适用范围。
- 不要向无权限的第三方提供原始素材包、游戏解密文件、账号凭据或绕过保护的工具。
- 如你是权利人并希望停止使用特定素材，请通过插件仓库或维护者联系方式提出。

继续阅读：[免责声明](DISCLAIMER.md)、[第三方声明](NOTICE.md)、[更新日志](CHANGELOG.md)。
