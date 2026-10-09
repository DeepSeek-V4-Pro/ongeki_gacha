# 曲目预览与多音源配置

## 使用

```text
/曲目预览 音击 Don't Fight The Music
/曲目预览 音击 鳥の詩 | Lia「AIR」
/曲目预览 2
/曲目预览 取消
```

歌手填写游戏曲库中的原始名称。先匹配本地曲库的曲名，再让音源返回曲名、完整歌手阵容与版本一致的结果。
仅统一大小写、全半角与空白，不猜测中英译名、艺名、CV、混音或现场版。上述命令是语法示例，不保证对应歌曲在音源中可用。
同名多版本一定先发图片列表；序号只对发起者和当前会话有效，60秒到期自动失效，重启也会取消。
曲绘来自所选游戏条目；缺少曲绘、图片下载/发送失败时，手动命令停止并报错，不会换用音源的唱片封面。

终极任务自动语音直接使用已经抽到的歌曲和歌手，不弹版本列表；任务接取成功后在数据库锁外处理。
自动失败不发报错，不撤销任务。关闭 `automatic_ultimate` 可只保留手动命令；`enabled` 控制整个曲目预览功能。

## 音源与登录

| `providers` 标识 | 接入方式 | 登录 |
| --- | --- | --- |
| `netease_api` | 配置 `netease_api_url`，兼容 NeteaseCloudMusicApiEnhanced | 插件的 `netease_cookie`，或该服务已有的登录态 |
| `netease` | 网易云公开搜索/详情/外链，直接请求 | 匿名；需要验证、会员权限或不可用时转下一个源 |
| `meting_tencent` | 配置 `meting_api_url`，QQ音乐 | Meting 服务端 `METING_COOKIE_TENCENT` |
| `meting_kugou` | 同一 Meting 端点，酷狗 | 服务端 `METING_COOKIE_KUGOU` |
| `meting_kuwo` | 同一 Meting 端点，酷我 | 服务端 `METING_COOKIE_KUWO` |
| `meting_netease` | 同一 Meting 端点，网易云 | 服务端 `METING_COOKIE_NETEASE` |
| `deezer` | Deezer 公开 API | 无登录；只提供短试听，需打开 `allow_preview_fallback` |

默认依次尝试表中音源。自建端点默认留空，对应音源未配置时跳过；默认直接可尝试的是匿名网易云，但可能被上游限制。
插件不内置第三方共享账号、Cookie 或未经验证的中转站。需要多平台完整音频时，请配置自己的服务；公共接口在线状态不保证。
所有兜底继续核对原游戏曲目，不会放宽歌手、换唱片封面或自动找同名翻唱。

### 网易云登录接口

运行 [NeteaseCloudMusicApiEnhanced](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced)，将服务地址填入
`netease_api_url`（例如 `http://127.0.0.1:3000`，不要附加 `/cloudsearch`）。
把自己的网易云 Cookie 填入 `netease_cookie`；留空则使用服务端登录态。
Cookie 仅放在发往这个明确配置的服务的 POST 正文中，不带到公共备用源或音频下载地址；该请求不跟随重定向。
请只填写自己管理的服务地址，远程服务使用 HTTPS；配置含 Cookie 时不要公开分享配置文件。

使用 `/cloudsearch` → `/song/detail` → `/song/url/v1`（standard音质）的接口，显式关闭跨平台 unblock 替换。
账号能否播放仍由上游决定；失效、无权限或地区限制会继续尝试其他源，手动命令最终汇总失败原因。
接口定义：[搜索](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced/blob/main/module/cloudsearch.js)、
[音频地址](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced/blob/main/module/song_url_v1.js)。

### 多平台兜底

运行 [metowolf/Meting-API](https://github.com/metowolf/Meting-API)，在 `meting_api_url` 填完整 API 端点，
例如 `http://127.0.0.1:3001/api`。设置服务端 `METING_URL` 使搜索返回的音频链接与配置端点保持同一源（协议、域名、端口）。
各平台 Cookie 在该服务的环境变量或 `cookie/tencent`、`cookie/kugou`、`cookie/kuwo`、`cookie/netease` 文件配置，
具体以 [Meting-API 文档](https://github.com/metowolf/Meting-API/blob/master/README.md) 为准。
Meting 标准协议不接受客户端传入各平台 Cookie，因此插件不提供无效的 `qq_cookie` 一类字段。

要求端点支持 `type=search` / `type=song` 的 JSON 列表（`title`、`author`、`url`），以及签名音频 URL 的重定向。
仅能返回歌单、没有搜索功能，或返回字段不同的“兼容 API”不能使用。

## 音频、缓存与错误

音频先校验完整时长，再压缩为单声道 MP3，通过 QQ `voice` / AstrBot `Record` 发送，由适配器转换为 QQ 支持的格式。
发送数据控制在5MiB以内，音质随长语音时长调整，避免超过MaiBot通信帧上限；不会因此截断歌曲。
安装 `requirements.txt` 中的 `imageio-ffmpeg`，或配置 `ffmpeg_path` 指向已有 FFmpeg。
AstrBot QQ 官方机器人还需要框架的 SILK 转码支持；OneBot/NapCat 等能否发送更长语音由适配器决定。

不截取固定30秒。默认最大1200秒（20分钟）、下载最大64MiB；超限报错，不发送截断结果。
检查实际解码时长与上游全长信息，发现短试听或截断时默认拒绝。启用 `allow_preview_fallback` 后，只有所有完整音源失败才使用片段。
正常成功只发送曲绘原图和语音，不加文字、边框或缩放。仅使用短试听兜底并成功发送时，补一句“试听片段”；自动失败仍静默。
Meting 标准响应没有全长，60秒以内按可能的试听片段处理；更长音频按原音源发送，但内部不标记为已验证完整版。

缓存仅保留转换后的 WAV 与来源标签，默认24小时、最多256MiB；下载临时文件在处理结束后删除。
默认每账号10秒间隔，同时最多处理3个请求；版本选择等待不占用音频处理名额。
网络、匹配、登录、大小、解码、曲绘和QQ发送错误会明确区分；QQ发送超时结果不确定，因此不自动重试同一发送。命令入口和发送RPC均声明了对应的处理超时。
纯数字曲名请指定游戏（如 `/曲目预览 舞萌 39`），避免与选择序号混淆。
离线测试与接口检查不代表真实QQ平台送达验收。

## 配置参考

MaiBot 在 `config.toml` 的 `[preview]` 分组配置；AstrBot 在 WebUI 的 `preview` 分组配置（相同字段）。
登录 Cookie 应填写在本地配置，不要提交到仓库。全部默认值：

```toml
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
