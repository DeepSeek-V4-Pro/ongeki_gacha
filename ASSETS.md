# 素材接入

本插件只负责游戏逻辑、数据索引和展示。卡面、角色立绘、界面图片、字体、养成图片与语音等游戏素材
不由本项目提供，也不属于项目代码的开源许可范围。

仓库与发布包只包含代码、文档、工具和 JSON 索引。需要图形或语音功能时，请仅接入你自行取得且有权使用的
本地素材；素材的取得、复制、使用和分发范围由使用者自行负责。运行时的卡池公告图、曲绘和官方漫画来自
公开页面，失败时会按功能设计回退或给出提示。

## 目录

```text
assets/
├─ card_data/         卡牌索引与玩家自备卡面
├─ ui/                抽卡结果图、揭示图使用的界面素材
├─ fonts/             渲染字体
└─ growth/
   ├─ *.json          养成目录、曲线、奖励与校验索引
   ├─ images/         好感页、解花与物品展示素材
   ├─ rewards/        称号、名牌与装饰预览
   └─ voices/         档案语音与事件语音
```

所有图片、字体和音频目录都可保持为空。插件会在缺少对应素材时改用文字、文字卡片或统一占位图。

## 卡面

卡面文件统一放在 `assets/card_data/`，推荐命名：

```text
ui_card_<6位卡牌ID>.png
```

也支持用户提供的角色图层与原图，由接入工具识别、缩放和合成：

```powershell
python tools/card_asset_tools.py scan --source .\本地素材
python tools/card_asset_tools.py compose `
  --source .\角色图层 `
  --layers .\通用图层 `
  --info assets\card_data\card_info_merged.json `
  --out temp\card_art
python tools/card_asset_tools.py import `
  --source .\成品卡面 `
  --info assets\card_data\card_info_merged.json `
  --dest assets\card_data
python tools/card_asset_tools.py verify
```

工具只读取命令行指定的本地文件，不会下载、搜索、扫描游戏包或修改远程数据。
如果目标目录已经存在部分卡面，默认导入流程会保留现有文件并更新索引中的可用状态。

## 界面与字体

抽卡结果图和揭示图使用的星级、MAX、解花印记与数字字体放入 `assets/ui/`。
缺少任意所需文件时，对应输出自动退化为文字。

常规分页图优先使用 `assets/fonts/` 中的 Noto Sans CJK SC Regular 与 Bold。
没有字体时会尝试系统字体；系统也没有合适字体时，常规回复退化为纯文字。

## 养成图片与语音

养成图片和语音同样只接受用户自行提供的本地文件。确认素材目录、索引和输出位置后，可使用：

```powershell
python -m ongeki_gacha.tools.build_growth_assets `
  --source <本地素材目录> --output ongeki_gacha\assets\growth
python -m ongeki_gacha.tools.build_growth_theme

python -m ongeki_gacha.tools.build_affection_voice_assets `
  --source <本地语音目录> --output ongeki_gacha\assets\growth
python -m ongeki_gacha.tools.build_event_voice_assets `
  --source <本地事件语音目录> --output ongeki_gacha\assets\growth
```

语音打包后可以执行自动校验并查看报告：

```powershell
python -m ongeki_gacha.tools.review_voice_assets `
  --root ongeki_gacha --report ongeki_gacha\temp\voice_review.json
python -m ongeki_gacha.tools.review_voice_assets `
  --root ongeki_gacha --approve
```

只有验收状态为 `verified` 的语音才会发送。若暂时不希望发送语音，可在配置中关闭
`[growth] voice_enabled`；其他养成功能不受影响。

## 自检

```powershell
python -m ongeki_gacha.tools.verify_growth_install
python -m ongeki_gacha.tools.verify_text_fallback
python tools/card_asset_tools.py verify
```

无素材自检会复制一份不含二进制素材的插件副本并执行一组命令，用于确认文字兜底仍然可用。

## 使用边界

- 不要使用来源不明或无权使用的素材。
- 不要公开分享原始游戏数据包、账号凭据或受版权保护的素材集合。
- 不要以本项目名义宣称获得游戏厂商或素材权利人的授权。
- 项目与 SEGA 及相关权利人无隶属、合作或认可关系。
- 如你是权利人并希望处理特定内容，请通过仓库提供的联系方式联系维护者。

详见 [免责声明](DISCLAIMER.md) 与 [第三方声明](NOTICE.md)。
