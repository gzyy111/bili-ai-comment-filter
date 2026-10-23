# B站评论 AI 号判定器（只读）

给任意一条 B站视频的评论区出一张表：每条评论判 **human / unsure / bot**，并写清判定依据。

**只读** —— 不回复、不点赞、不关注、不碰 `space` 用户主页接口。拿不准的一律判 `unsure`，按「不回」处理。

2026-10-21 实测：**1,705 条真实评论**，热门区误伤 **1 / 1,140（0.09%）**；人工标注 60 条**精确率 95%**。
细节见 [docs/BENCHMARK.md](docs/BENCHMARK.md)。

---

## 一、它解决什么问题

评论区里那批「大肥鱼」「本喵」号：有名字、有头像、有表情包、有固定人设，说话比真人还客气。
自动回复机器人认不出它们，就会把 AI 当真人，一条一条认真回。

这个工具把「感觉」变成可复核的分数：账号画像 + 文本特征 + 跨视频行为三层打分，
输出一张能直接人工核对、也能直接接进判定流程的表。

> 它不识别「哪条评论是 AI 写的」，只回答一个更保守的问题：**这条评论值得回吗**。

## 二、三层判据

| 层 | 看什么 | 典型分值 |
|---|---|---|
| 第 1 层 · 账号画像 | 等级、签名、挂件、名牌、大会员、认证、硬核会员、昵称家族 | Lv0–1 `+3`，Lv2 `+2`，空签名 `+1`，其余零配饰各 `+0.3`，昵称含「大肥鱼/鲸鱼娘」等 `+0.8` |
| 第 2 层 · 文本特征 | 自称拟物（本鱼/本喵/作为AI）、模板开场（有一说一/说实话）、@人+长句无表情、标点过规整、套话收尾、人设口吻、开头喊主人 | 单项 `+0.4 ~ +2.0`；短评 `-1.5`、表情短评 `-1.0` 作为真人信号 |
| 第 3 层 · 跨视频行为 | 同一 mid 出现在几条被扫视频下、同一句话重复几次 | 3 条以上 `+1.8`，2 条 `+1.2`，长句复读 `+1.5` |

判定：`total >= 4.8` 且带文本/行为证据 → **bot**；`3.0 ~ 4.79` → **unsure**；其余 → **human**。

## 三、快速开始

只用 Python 标准库，**无第三方依赖**，Python 3.9+。

```powershell
$env:PYTHONUTF8="1"
python tools\bili_bot_filter.py --bv BV1U1nF6PE7e --pages 2 --out out\report.md
```

Windows 直接双击 `run.bat`，或 `run.bat BV1U1nF6PE7e`（报告写到 `out\report.md`）。

批量扫一组稿件（每行一个 BV，`#` 开头是注释）：

```powershell
python tools\bili_bot_filter.py --bvs-file examples\bvs.txt --pages 1 --out out\report.md
```

## 四、关于 cookie（重要）

游客态调 `x/v2/reply` **每页只回 3 条**，而且拿不到楼中楼。要满 20 条/页必须带登录 cookie：

```powershell
$env:BILI_COOKIE="SESSDATA=...; bili_jct=...; buvid3=...; DedeUserID=..."
```

cookie 等同密码：

- 只放本机环境变量或本机文件（`BILI_COOKIE_FILE`），**别提交、别截图、别贴进 issue**
- `.gitignore` 已经挡掉 `cookies.json`、`*.cookie`、`.env`

## 五、输出长什么样

见 [`examples/verdict_sample.md`](examples/verdict_sample.md)（已脱敏：账号名压成「前 2 字·末 1 字」，mid / rpid 全部隐去）。

| # | 判定 | 分 | 账号 | 等级 | 评论 | 依据 |
|---|---|---|---|---|---|---|
| 1 | **bot** | 8.8 | 某·某（mid 隐去） | 2 | @某人 主人 一句话没说话也没画面… | Lv2 / 空签名 / 无挂件/名牌/会员/认证 / 人设口吻:主人 / 开头喊主人 |

## 六、判定分级与默认动作

| 判定 | 分数 | 默认动作 | 理由 |
|---|---|---|---|
| `bot` | ≥ 4.8 | 不回 | 账号+文本+行为多点同时命中 |
| `unsure` | 3.0 ~ 4.79 | 不回 | 拿不准就不回，误伤真人成本远高于漏掉一个 bot |
| `human` + `needs_llm` | < 3.0，且 ≥30 字、无表情、@人或多个逗号 | 交大模型复核 | 老号伪装集中在这一档 |
| `human` + 短评/低价值 | 长度 < 6 或纯符号 | 不回 | 没必要 |
| `human` | 其余 | 可回 | 受下面的限流约束 |

**为什么默认拒绝**：判错的代价不对称——误判真人只是少回一句，误判 bot 是把自己的账号变成 bot 的对话搭档。

## 七、进阶：自动回复流水线（默认 dry-run）

```powershell
python tools\auto_reply.py --gen rules                    # 单次 dry-run：只判、只生成
python tools\auto_reply.py --gen rules --watch --interval 300   # 常驻轮询
python tools\auto_reply.py --gen llm --live --yes --max 1       # 真发，每轮最多 1 条
```

- 去重 / 限流落在 `out/reply_state.db`：同一评论只处理一次、同一用户 24h 只回一次、同一稿件 1h 最多 3 条、命中 `601` 退避 60s。
- `--gen llm` 走 OpenAI 兼容接口（`OPENAI_BASE` / `OPENAI_KEY` / `OPENAI_MODEL`），失败自动退回 `rules`。
- **`--live` 会在你的账号下真的发言**：必须先设 `BILI_EXPECTED_UNAME` 校验登录的是谁的号，再加 `--yes` 二次确认。
- 详细的四档命令、分级去向、口吻规范见 [docs/PIPELINE.md](docs/PIPELINE.md)。

⚠️ B站社区规范对第三方自动化有限制，频率高会 `601` / `-352` 甚至封号。这部分风险自负，建议先 dry-run 跑几天，人工核对判定表再考虑放量。

## 八、实测数字（可复现）

| 组 | 视频 | 评论 | bot | unsure | human |
|---|---|---|---|---|---|
| DSH 圈（bot 密集） | 18 | 565 | 154 | 127 | 284 |
| 热门区（真人密集） | 12 | 1,140 | **1** | 41 | 1,098 |

- 热门区误伤 **1 / 1,140 ≈ 0.09%**，唯一那条是「账号已注销」（本来也不该回）
- 人工标注 60 条：TP 57 / FP 3 / FN 0 → **精确率 95%，召回 100%**（只对这 60 条成立）
- 漏掉的 3 条全是 Lv4–6 老号，且**全部**被 `needs_llm` 标记命中 → 「规则初筛 + LLM 只复核标记项」能兜住

复现链路（`collect_corpus.py` → `reclassify.py` → `metrics.py`）见 [docs/BENCHMARK.md](docs/BENCHMARK.md) 第六节。

## 九、目录

```
tools/bili_bot_filter.py   判定器主程序（只读）
tools/collect_corpus.py    采集语料（含楼中楼）并打初判标签，产出 CSV 供人工标注
tools/reclassify.py        在已采语料上重打分，不联网，输出分组统计与误判样本
tools/metrics.py           在人工标注样本上算精确率/召回率
tools/dump_bucket.py       打印指定桶的样本，方便人工标注
tools/auto_reply.py        自动回复流水线（判定→分级→生成→去重限流→发送，默认 dry-run）
tools/live_test_send.py    一次性发送链路验证：发一条 → 回读 → 立即删除
docs/DESIGN.md             调研：现有自动回复项目盘点 + 判据设计 + 风控事实
docs/BENCHMARK.md          判定器 v2 实测报告（语料口径、v1→v2 改动、标注成绩）
docs/PIPELINE.md           自动回复流水线说明（四档命令、分级去向、未做项）
examples/                  脱敏输出样例、BV 清单、人工标注样本
run.bat                    一键跑单条稿件
```

`reclassify.py` / `dump_bucket.py` 里的 `DSH` 常量是作者自己的 18 条稿件清单（用来把语料分成「bot 密集」和「真人密集」两组），换成你自己的分组即可。

## 十、已知局限（先说清楚）

1. **95% 不是全站准确率**，只对 60 条人工标注成立；标注扩到 300+ 条之前，阈值只能算初值。
2. **老号伪装挡不住**：Lv4–6、学着真人写作、人设词藏在句子里的号，规则会漏；这类只能交 LLM 复核。
3. **字段来源受限**：粉丝数、投稿数、注册时间不在评论接口里，而 `x/space/wbi/acc/info` 风控很凶（`-352` / `-412`），所以判据全部压在评论接口自带字段 + 本地累计行为上。
4. **接口一改就失效**：B站 随时可能改返回结构或加风控。
5. **语料不入库**：仓库不含任何真实评论文本与账号 mid，所以上面那些数字你无法逐条复核，只能照 `collect_corpus.py` 自己采一遍。

## 十一、参考

调研阶段读过、对比过的项目见 [CREDITS.md](CREDITS.md)。本仓库**不含任何第三方代码**。

## 十二、许可

未附加 `LICENSE` 文件：代码版权归作者所有。要复用请先开 issue 说一声。
CREDITS 里列出的项目各自遵循其原许可。
