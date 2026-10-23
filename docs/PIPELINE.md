# 自动回复流水线 · 2026-10-21（v2：判定/生成/轮询/发送 全通）

> 账号：鬼赵阳阳（mid 30927858）。凭证 `本机 cookies.json（不入库）`。
> 本轮把「占位草稿」「没有轮询」「发送未验证」三个缺口补掉；发送链路用两次真实请求验证后立即删除，无残留。

---

## 一、四档命令

| 用途 | 命令 |
|---|---|
| 单次 dry-run（只判只生成） | `python tools\auto_reply.py --gen rules` |
| 常驻轮询（每 5 分钟） | `python tools\auto_reply.py --gen rules --watch --interval 300` |
| 发送链路自检（发→回读→删） | `python tools\auto_reply.py --probe-rpid <评论rpid> --only-bv <BV>` |
| 真发（每轮最多 N 条） | `python tools\auto_reply.py --gen llm --live --yes --max 1` |

`--live` 前会先 `nav` 校验账号必须是「鬼赵阳阳」，不是就中止。

---

## 二、处理分级（每一档都有明确去向，没有占位文本）

| 分级 | 条件 | 动作 |
|---|---|---|
| bot | 账号分+文本分 ≥4.8 且有文本证据 | `skip` |
| unsure | 3.0–4.79 | `skip` |
| human + 低价值 | 长度 <6 或纯符号 | `skip_low_value` |
| human + 需复核 | ≥30 字、无表情、@人或多逗号 | 有 LLM → 模型判 human/bot；无 LLM → `skip_no_model` |
| human + 正常 | 其余 | `reply`（受下面三条限流约束） |

限流（都落在 `out\reply_state.db`）：

- 同一评论只处理一次（`rpid` 主键）
- 同一用户 24h 内只回一次
- 同一稿件 1h 内最多 3 条
- 命中 `601` 自动退避 60s

---

## 三、生成回复（三档，没有占位）

- `--gen rules`（默认）：按评论意图从本片事实库取答案，确定性、不联网。
  - `问一下她听得到我们的语音吗` → `听得到，语音会先在本地转成文字再回（faster-whisper，不上传第三方）。`
  - `这已经是鱼站了，全是流窜ai` → `确实，评论区现在一半是 bot。我这边在加过滤，尽量只回真人。`
- `--gen llm`：OpenAI 兼容接口（`OPENAI_BASE` / `OPENAI_KEY` / `OPENAI_MODEL`），失败自动退回 rules，并在库中记 `llm_fallback:`。
- `--gen none`：只判定不生成（调试用）。

口吻规范：中性 UP 主——短、具体、直接答；不发「感谢支持」模板；不加 emoji；不自曝 AI；不引流。

---

## 四、本轮证据

| 验证项 | 结果 |
|---|---|
| 单次 dry-run（3 稿件 / 40 条评论） | 可回 3、送 LLM 3、bot 10、unsure 24；生成的都是真文本 |
| 轮询 2 轮（间隔 20s） | 第 1 轮处理 40 条；第 2 轮 **40 条全命中重复**，去重生效 |
| 发送链路（`live_test_send.py`） | rpid `已隐去` 挂在 root `已隐去` 下，回读 true；`del` 返回 `code=0`；删除后回读 false |
| 发送链路（流水线 `--probe-rpid`） | rpid `已隐去` 同样 发→回读 true→删→回读 false |

原始输出见 `out\live_test_20261021.md`。

---

## 五、还没做的

1. **真实回复尚未正式发出**：按你的选择只做了「测试回复后立即删除」。正式上线时用 `--live --max 1` 先放一条。
2. **楼中楼没扫**：只处理根评论；别人在你回复下面的追问不会自动接。
3. **LLM 未配 key**：`needs_llm` 一律 `skip_no_model`（保守不回）。配好 `OPENAI_KEY` 后这类评论才会被判并回复。
4. **无计划任务**：`--watch` 是前台常驻，要开机自启得挂计划任务。

---

## 六、文件

- `tools\auto_reply.py`：主程序（dry-run / watch / probe / live）
- `tools\bili_bot_filter.py`：判定器
- `tools\live_test_send.py`：独立的一次性发送验证
- `out\live_test_20261021.md`：发送验证原始记录
- `out\reply_dryrun_20261021.md`、`out\reply_watch_2cycles_20261021.md`：dry-run 与轮询日志
- `out\reply_state.db`：去重/限流库
