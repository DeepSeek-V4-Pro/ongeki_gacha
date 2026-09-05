# 卡面素材接入工具箱

本插件只分发**接入工具**，不提供抓取、下载、解包、解密或素材来源脚本。
请先自行确认并取得有权使用的本地卡面文件，再使用工具箱把它们整理成插件需要的
`assets/card_data/` 格式。

工具箱不会修改插件版本号，也不会向网络发送任何请求。

## 前置条件

- Python 3.10 或更高版本。
- `Pillow`：插件运行依赖已包含，也可执行 `pip install -r requirements.txt`。
- 自己已经取得的本地图片，支持 PNG / WebP / JPEG 等常见格式。
- 下面命令在插件目录执行；相对路径默认以脚本所在插件目录为基准。

## 支持的文件

工具箱主要识别以下几类文件：

```text
ui_card_100001.png                              成品卡面
UI_Card_Chara_100001_P.webp                     RinNET 角色 P 图层
ui_card_chara_100001_p.png                      本地解包后的角色 P 图层
ui_card_chara_100001.png                        普通角色图层
```

文件名中的 `100001` 可以是任意 6 位卡牌 ID。`icon`、`holo`、`mask` 等附属图层
不会被误当作卡面。如果拿到的文件是随机文件名，可使用 `--mapping` 提供
`文件 -> ID` 映射。

## 快速流程

### 1. 扫描已有素材

先看看本地目录里有哪些可用文件，以及还缺多少张：

```powershell
python card_asset_tools.py scan --source .\素材目录
```

可以按标准文件名直接扫描；如果文件名是随机的，可额外提供映射：

```powershell
python card_asset_tools.py scan `
  --source .\素材目录 `
  --mapping .\映射.csv `
  --report temp\scan.json
```

映射 CSV 的格式：

```csv
id,file
100001,随便取的名字.png
100002,另一张.webp
```

映射 JSON 也可以：

```json
{
  "100001": "随便取的名字.png",
  "100002": "另一张.webp"
}
```

### 2. 从角色图层合成

如果你拿到的是角色 `_P` 图层，需要通用背景/边框/稀有度标记时，可以先合成：

```powershell
python card_asset_tools.py compose `
  --source .\角色图层 `
  --layers .\通用图层 `
  --info assets\card_data\card_info_merged.json `
  --out temp\card_art
```

`compose` 生成的是 **768×1052 近似净卡面**：

- 包含背景、边框、属性/稀有度/学年图标和近似的卡名文字；
- 不包含动态 HUD、holo、星级、解花标记和底部 ID 信息条；
- 不等同于 RinNET 前端逐像素复刻。

如果不需要卡名文字，可加 `--skip-text`：

```powershell
python card_asset_tools.py compose `
  --source .\角色图层 `
  --layers .\通用图层 `
  --skip-text `
  --out temp\card_art
```

如果暂时没有通用图层，只想检查角色图，可使用 `--allow-raw` 直接导出角色图；
但插件默认标准卡面需要背景和边框，建议只在临时预览时使用。

### 2.1 浏览器版合成（复用上游拼接逻辑）

此前验证过的上游合成脚本采用浏览器叠加图层的方式，对字体、卡名旋转和
图层顺序的还原更接近上游展示。插件内附的对应脚本是 `compose_card_art.py`：

```powershell
python compose_card_art.py `
  --chara-dir .\角色图层 `
  --layers-dir .\通用图层 `
  --info assets\card_data\card_info_merged.json `
  --out temp\card_art_browser
```

它会直接输出 `ui_card_<6位ID>.png`，同样不包含底部 ID 信息条（抽卡插件渲染时
会自动叠加该信息条）。需要已安装 Playwright 和 Chromium/Edge：

```powershell
pip install playwright
playwright install chromium
```

如果本机已有 Edge，脚本会自动使用；也可用 `--browser-path` 指定。
浏览器版适合有 Playwright 环境的用户；没有时使用上文的 Pillow 版即可。

只合成指定 ID：

```powershell
python card_asset_tools.py compose `
  --source .\角色图层 `
  --layers .\通用图层 `
  --ids 100001,100002,100003 `
  --out temp\card_art
```

浏览器版也用 `--ids` 过滤；合成完成后可把这批成品作为后续 `import` 的素材源：

```powershell
python compose_card_art.py `
  --chara-dir .\角色图层 `
  --layers-dir .\通用图层 `
  --ids 100001,100002,100003 `
  --out temp\card_art_browser

python card_asset_tools.py import `
  --source temp\card_art_browser `
  --dest assets\card_data
```

### 3. 导入插件数据目录

已经有 `ui_card_<6位ID>.png` 成品时：

```powershell
python card_asset_tools.py import `
  --source .\成品卡面 `
  --info assets\card_data\card_info_merged.json `
  --dest assets\card_data
```

如果素材目录里同时包含角色图层，可以自动优先使用成品，缺少成品且提供
`--layers` 时才自动合成：

```powershell
python card_asset_tools.py import `
  --source .\素材混合目录 `
  --layers .\通用图层 `
  --dest assets\card_data
```

只接入部分 ID 时加 `--ids`：

```powershell
python card_asset_tools.py import `
  --source .\成品卡面 `
  --ids 100001,100002,100003 `
  --dest assets\card_data
```

默认会更新目标目录中的 `imagePresent`，因此分批导入不会丢失之前已经接入的卡面。
如果你只在完整包导入且不希望修改目标 JSON，可使用：

```powershell
python card_asset_tools.py import --source .\成品卡面 --keep-json
```

注意：`--keep-json` 只适合一次性导入完整卡面；分批导入请保留默认行为。

不想把素材放进插件目录时，可以导入到独立目录，再在插件配置中填写绝对路径：

```powershell
python card_asset_tools.py import `
  --source .\成品卡面 `
  --dest .\my_card_data
```

```toml
[assets]
cards_dir = "<你的素材目录>"
card_info_json = "<你的素材目录>/card_info_merged.json"
```

### 4. 校验

接入后检查文件、尺寸和 manifest：

```powershell
python card_asset_tools.py verify
```

也可以指定自定义目录：

```powershell
python card_asset_tools.py verify `
  --dest .\my_card_data `
  --info .\my_card_data\card_info_merged.json
```

较慢但完整的哈希校验仍然使用原有脚本：

```powershell
python sync_card_data.py --check
```

## 安全与版权提醒

- 工具箱不会下载网络资源，不会扫描游戏包、不会执行解密或绕过保护。
- 工具箱只处理用户本地已经取得的图片文件，也不会上传或分享这些文件。
- 请在取得、复制、使用素材前自行确认权利和适用场景。
- 卡面、角色图层、UI 素材权利归 SEGA 或相应权利人所有，不属于项目代码授权范围。

## 分发说明

- 插件发布包只包含脚本、说明和插件原有数据，不附带受版权保护的卡面。
- 脚本没有写死用户机器路径；素材目录、信息 JSON、输出目录均由命令行传入。
- `card_asset_tools.py` 只依赖插件已有的 `Pillow`。
- `compose_card_art.py` 的浏览器合成为可选功能，需要用户自行安装
  `playwright`；安装浏览器后脚本会优先使用本机 Edge/Chrome 或 Playwright 自带 Chromium。
- 如果只分发脚本而不分发素材，请在插件配置中保留默认 `assets/card_data` 路径，
  或让用户按本说明把素材接入自己的数据目录。

继续阅读：

- [CARD_ARTWORK_SOURCES.md](CARD_ARTWORK_SOURCES.md)
- [DISCLAIMER.md](DISCLAIMER.md)
- [NOTICE.md](NOTICE.md)
