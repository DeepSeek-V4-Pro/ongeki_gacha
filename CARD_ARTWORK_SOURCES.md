# 卡面素材获取说明

本插件只负责本地模拟、展示和抽卡逻辑。**卡面 PNG、角色图层、UI 素材及卡牌元数据均不属于插件代码的 LICENSE 范围**，其权利归 SEGA、相关角色或素材权利人所有。

插件发布包通常不附带全部高清卡面。原因是：

- 版权归属不随插件代码授权；
- 发布包体积过大；
- 不同用户对素材使用范围和来源的授权情况不同；
- 公开渠道中的成品图、缩略图与本地标准卡面（768×1052）并不等同。

因此，请**自己负责取得和使用素材**，并在确认有权使用后再放入 `assets/card_data/`，或将插件配置指向你自己的素材目录。

---

## 1. 公开可查询的官方渠道

### SEGA 官方オンゲキ信息站

- 首页：<https://info-ongeki.sega.jp/>
- 官方发布文章：<https://info-ongeki.sega.jp/category/game/>
- CARDMAKER 卡池公告：<https://info-ongeki.sega.jp/category/cardmaker/>
- 官方活动/新曲/新卡页面通常包含公开的宣传图和卡面预览。

这个渠道适合：

- 确认某张卡是否真实存在；
- 获取卡名、稀有度、活动日期、卡池和官方宣传图；
- 核对 `card_info_merged.json` 中缺失的 1.60 新卡 ID；
- 下载公告中的公开图片，作为“预览/对照图”，但不一定等同于最终卡面图层。

官方文章中的图片路径一般形如：

```text
https://info-ongeki.sega.jp/wp-content/uploads/YYYY/MM/<hash>.png
https://info-ongeki.sega.jp/wp-content/uploads/YYYY/MM/<hash>-219x300.png
```

你可以在页面源码或浏览器开发者工具的 Network / Images 面板中查找。
插件发布包不包含抓取脚本，也不提供自动抓取支持；
请自行确认权限后使用官方公开图片。

### オンゲキ官方主站

- 官方主站：<https://ongeki.sega.jp/>
- 角色/插画页面：<https://ongeki.sega.jp/character/>
- 卡牌取扱说明：<https://ongeki.sega.jp/card/>

官方主站适合了解游戏背景、角色设定和官方公开插图，但“カードの取扱いについて”页面主要说明实体卡和取扱规则，不提供完整卡面下载库。

### オンゲキ-NET（需用户自己的官方账号）

- 官方连动网站：<https://ongeki-net.com/ongeki-mobile/>

如果你拥有自己的 SEGA ID / Aime 账号，可以在允许的范围内登录官方在线服务，查看自己的游戏数据、活动信息、公告以及官方公开页面。插件不会替你登录、不会保存你的账号密码。

请自行遵守《利用規約》和账号安全要求。

---

## 2. 公开数据库 / 社区资料

以下渠道只用于查询公开元数据或公开接口返回的数据，**不保证提供可直接分发的卡面素材**。

### OTOGE DB

- 地址：<https://otoge-db.net/ongeki/>

OTOGE DB 是公开的曲目/谱面数据库，通常包含版本、曲名、难度等信息。它可以帮助你确认当前版本和新增曲目，但一般不是卡面图库。

### AquaDX / AquaNet 公开 API

- 项目仓库：<https://github.com/MewoLab/AquaDX>
- 公开实例：<https://aquadx.net>

公开 API 可查询玩家排名、最近游玩记录和卡组 ID。例如：

```text
POST https://aquadx.net/aqua/api/v2/game/ongeki/user-summary
POST https://aquadx.net/aqua/api/v2/game/ongeki/recent
```

通过该接口可以发现本地尚未收录的卡 ID（例如 1.60 新卡），但接口**不提供“卡名 + 卡面”**的完整静态卡表。它只能帮助你确认：

- 某 ID 是否真实出现在 1.60 实机数据中；
- 出现日期、活动、曲目和卡组位置；
- 哪些 ID 仍缺少本地素材。

AquaDX 是社区项目，不是 SEGA 官方服务；使用时请自行确认访问权限、条款和素材用途。

### Artemis / 公开静态数据仓库

- 仓库中的 `static_gachas.csv`、`static_gacha_cards.csv` 等公开数据可用于梳理卡池日期、UP、选择卡和权重。
- 仓库中的 `game_card.json`、`game_chara.json` 通常只包含元数据，不包含 PNG/WebP 卡面。

公开仓库只能用于查阅元数据和实现本地逻辑，不代表你可以直接复制、分发其中涉及的受版权保护素材。

---

## 3. 本地已有素材的检查方式

如果你已经具有合法来源的卡面或更新包，可以按以下顺序检查：

### 3.1 检查插件默认数据目录

```powershell
python sync_card_data.py --check
```

如果目录中没有 `ui_card_*.png`，插件会提示“卡牌数据不可用”。

### 3.2 使用你自己的素材目录

```powershell
python sync_card_data.py `
  --source ./path/to/cards `
  --source-json ./path/to/card_info_merged.json `
  --check
```

也可以在插件配置中修改：

```toml
[assets]
cards_dir = "./path/to/cards"
card_info_json = "./path/to/card_info_merged.json"
```

### 3.3 处理本地已有的游戏/更新包

如果你已经合法拥有本地游戏包、更新包或解包中间文件，可以自行准备扫描工具，
仅用于确认卡牌元数据、ID 和角色图层，不要直接上传或公开分享受版权保护的原始素材。

完整的解包流程需要遵守：

- 不得绕过 DRM / 加密保护；
- 不得上传或公开分享受版权保护的原始素材包；
- 仅用于个人本地研究、娱乐或已获授权的用途；
- 发现无授权素材时应立即停止使用并联系权利人/维护者。

### 3.4 使用素材接入工具箱

拿到本地图片后，可使用插件内附的 `card_asset_tools.py` 完成识别、合成、导入和校验：

```powershell
# 扫描本地素材，查看成品/角色图层与缺口
python card_asset_tools.py scan --source ./素材目录

# 将成品卡面接入插件数据目录
python card_asset_tools.py import --source ./成品卡面

# 从角色图层合成（需提供通用图层）
python card_asset_tools.py compose `
  --source ./角色图层 `
  --layers ./通用图层 `
  --out temp/card_art

# 校验接入结果
python card_asset_tools.py verify
```

该脚本只处理用户已经取得的本地图片，不包含抓取、下载、解包或解密功能。
支持文件名映射、分批导入和独立数据目录；详细说明见
[CARD_ASSET_TOOLS.md](CARD_ASSET_TOOLS.md)。

如果需要更接近上游图层/字体排版的合成结果，可改用浏览器版
`compose_card_art.py`（需 Playwright，仍然不联网下载素材）。

---

## 4. 推荐的自行核对流程

1. 在官方信息站查询活动日期、卡名、稀有度和卡池公告。
2. 用官方宣传图确认卡面样式和活动主题。
3. 如果自己拥有合法来源的本地包/更新包，扫描出对应 `Card.xml` 和角色图层。
4. 使用 `card_asset_tools.py scan` 检查本地素材是否齐全。
5. 使用 `card_asset_tools.py import` 接入成品或合成角色图层。
6. 使用 `sync_card_data.py --dry-run` 预览将同步多少张卡。
7. 确认来源和权限后运行 `sync_card_data.py`。
8. 插件发布包内运行 `sync_card_data.py --check` 做完整性校验。

---

## 5. 常见问题

### 为什么不随插件提供全部卡面？

因为版权不属于项目代码，且公开渠道中的成品图、缩略图和本地标准图层并不总是等价。插件维护者不能替用户确认其获取、复制和使用素材的权限。

### 为什么插件已经有 `card_info_merged.json`，却仍然提示卡图缺失？

元数据 JSON 不需要图片；显示 `/卡图` 和合成抽卡结果才需要 `ui_card_*.png`。请检查 `assets/card_data/` 目录或 `assets.cards_dir` 配置。

### 官方宣传图可以直接用来做卡面吗？

通常不建议。官方宣传图可能经过裁切、合成、缩小或加文字/边框，尺寸也不一定是 768×1052。它适合做预览、对照和确认，不应直接伪装成完整标准卡面。

### 我已确认有权限，应该放哪些文件？

建议直接使用 `card_asset_tools.py import --source <素材目录>`，
脚本会自动识别/重命名、转换尺寸、更新 `imagePresent` 并重建校验清单。
也可以手动按以下命名放入 `assets/card_data/`：

```text
ui_card_<6位ID>.png
```

例如：

```text
ui_card_104490.png
```

元数据中的 `imageFile` 会决定实际文件名；默认命名规则为 `ui_card_<6位ID>.png`。

---

## 6. 重要提醒

- 本项目与 SEGA 无隶属、授权、合作或认可关系。
- 开源许可证不自动覆盖 SEGA 或其他权利人的卡面、角色、UI 和商标素材。
- 请在获取、复制、使用、分发前自行确认权利和适用范围。
- 不要向无权限的第三方提供原始素材包、游戏解密文件、账号凭据或绕过保护的工具。
- 如你是权利人并希望停止使用特定素材，请通过插件仓库或维护者联系方式提出。

具体责任范围和限制，请同时阅读：

- [DISCLAIMER.md](DISCLAIMER.md)
- [NOTICE.md](NOTICE.md)

**最后一句：插件只提供本地模拟逻辑；卡面从哪里、以什么方式取得，由你自己负责任地决定。**
