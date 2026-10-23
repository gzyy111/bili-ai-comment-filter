# CREDITS / 参考来源

本仓库的代码是自己写的（2026-10-21 一轮实测沉淀），**没有复制任何第三方实现**。
下面这些项目是调研阶段逐个读过 README / 关键代码的对象，判据设计和接口坑位有它们的贡献，按原许可保留出处。

## 一、自动回复项目（调研对象）

| 项目 | 当时状态 | 用了它的什么 |
|---|---|---|
| [chenluQwQ/bilibili-ai-bot](https://github.com/chenluQwQ/bilibili-ai-bot) | 30★，2026-10 仍在更新，Python + Flask 面板 | 「评论监听 + LLM 回复 + 记忆」的整体形态；结论是它的过滤只到关键词 + 好感度拉黑，没做账号画像，所以本仓库从这一层补 |
| [huangwelldone/AutoReplyBilibili](https://github.com/huangwelldone/AutoReplyBilibili) | 7★，2026-06，依赖豆包网页版 | 只抄思路：网页版自动化太脆，判定与生成解耦更稳 |
| [linzeliang1222/bilibili-autoreply](https://github.com/linzeliang1222/bilibili-autoreply) | 6★，2026-04，Selenium 点页面 | 反面参考：按关注状态回固定话术，本质是引流工具 |
| [zwzPromaxPlus/BiliBot](https://github.com/zwzPromaxPlus/BiliBot) | 1★，2026-02 | 小而简，可读性参考 |

## 二、接口与风控事实

- B站 评论接口 `x/v2/reply`：登录态 `ps=20` 才回满 20 条，游客态只回 3 条；楼中楼走 `x/v2/reply/reply`
- 账号画像字段（`level_info` / `sign` / `pendant` / `nameplate` / `vip` / `official_verify` / `is_senior_member`）**全部在同一次评论响应里返回**，不需要额外请求
- `x/space/wbi/acc/info`、`x/space/wbi/arc/search` 风控很凶（`-352` / `-412`），本仓库刻意不碰
- 发送侧：`x/v2/reply/add` 需要 `csrf`（`bili_jct`）；频率过高返回 `601`，退避后重试

## 三、语料与合规

- 所有实测语料（1,705 条评论）**不入库**，只保留统计结论与脱敏样例
- 样例里的账号名压成「前 2 字 · 末 1 字」，mid / rpid 全部隐去，评论文本截断到 40 字
- 采集时按 `0.7 ~ 0.9s` 间隔限速，只读公开评论，未操作任何账号

## 四、工具链

- Python 3.9 标准库（`urllib` / `json` / `re` / `sqlite3` / `csv`），无第三方依赖
- 视频里提到的 DSH 插件环境与本仓库无关，本仓库是独立 CLI
