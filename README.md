# 音击抽卡模拟器

当前版本：1.1.0

反馈或咨询：[GitHub Issues](https://github.com/DeepSeek-V4-Pro/ongeki_gacha/issues)

一个基于 MaiBot 的本地娱乐抽卡模拟器，用于模拟音击卡牌收集与抽卡体验。

常见命令：

```text
/签到
/月卡
/抽卡 1
/抽卡 5
/抽卡 11
/抽卡 常驻
/点数
/奖励 @用户 <点数>（管理员）
/概率
/卡册
/卡图 <ID>
/卡池
/天井
/规则
/帮助
```

插件默认按照历史官方 CARDMAKER 卡池每 15 天轮换，并应用卡池内 UP 权重；可在配置中切换为按官方日期选池。另有常驻池，可用 `/抽卡 常驻` 单独抽取。`/卡池` 会同时发送当期官方活动图。

奖励采用“日常小额 + 7 天/15 天周期大额 + 月度加成 + 囤点档位”的结构，适合长期签到和攒够点数后再抽。

详细说明：[USAGE.md](USAGE.md)  
免责声明：[DISCLAIMER.md](DISCLAIMER.md)  
第三方声明：[NOTICE.md](NOTICE.md)  
更新日志：[CHANGELOG.md](CHANGELOG.md)  
开源协议：[LICENSE](LICENSE)
