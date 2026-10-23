# -*- coding: utf-8 -*-
"""B站评论自动回复流水线（判定 → 分级 → 生成 → 去重/限流 → 发送）。

默认 dry-run：只判、只生成、写日志，不发。
    python auto_reply.py                          # 单次 dry-run
    python auto_reply.py --watch --interval 300   # 常驻轮询（每 5 分钟）
    python auto_reply.py --live --yes --max 1     # 真发（每次最多 1 条）

生成回复三档（--gen）：
    rules  默认。按评论意图从本片已知事实里取答案，确定性、不联网。
    llm    走 OpenAI 兼容接口（OPENAI_BASE / OPENAI_KEY / OPENAI_MODEL 环境变量）。
    none   只判定不生成（调试用）。
needs_llm 的评论：有 LLM 就让模型判一次「真人/机器人」，判为人且生成出回复才回；
没有 LLM 就明确跳过（no_model），不会留下占位文本。
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bili_bot_filter as F  # noqa: E402

# 凭证与账号护栏全部走环境变量，源码里不留任何本机路径与账号。
#   BILI_COOKIE_FILE     cookie JSON 路径（默认仓库根目录的 cookies.json）
#   BILI_EXPECTED_UNAME  期望登录的昵称；--live 必须设，防止登错号发错人
#   BILI_MY_MID          自己的 mid，自己的评论不处理
COOKIE_FILE = os.environ.get(
    "BILI_COOKIE_FILE", os.path.abspath(os.path.join(HERE, "..", "cookies.json")))
EXPECTED_UNAME = os.environ.get("BILI_EXPECTED_UNAME", "").strip()
MY_MID = os.environ.get("BILI_MY_MID", "").strip()

# 本片/本插件的事实库：规则生成只从这些事实里取答案，避免瞎编
FACTS = [
    (r"语音|听得到|听到|说话|声音", "听得到，语音会先在本地转成文字再回（faster-whisper，不上传第三方）。"),
    (r"唱歌|唱一|来首|来一首|乌鸦|晴天|语音条|翻唱", "唱歌走本地真唱链路，回一条 30 多秒的语音条，不联网也能量。"),
    (r"表情包|表情|图库", "表情包按语境从 5624 张里挑，会克制，不会刷屏。"),
    (r"装|安装|下载|部署|怎么用|教程|配置", "安装看置顶评论里的仓库 README，Windows 直接双击装。"),
    (r"收费|免费|多少钱|花钱|token|费用", "插件本体免费，本地那部分不花钱；模型调用看你接哪家。"),
    (r"源码|代码|仓库|github|开源", "源码在置顶评论里，仓库名搜「鲸鱼娘机器人」。"),
    (r"安全|隐私|会不会被|封号|风险", "她只读你发进来的文字，不碰聊天记录以外的数据；自动回复是我这边单独控制的。"),
    (r"鱼站|全是ai|流窜ai|机器人占领|找不到几个活人|鱼灾|鱼塘|大肥鱼.*多", "确实，评论区现在一半是 bot。我这边在加过滤，尽量只回真人。"),
    (r"怎么@|艾特|叫我", "在你的评论里 @ 我，或者在视频下留问题，我会看。"),
]
GENERIC_POS = re.compile(r"厉害|牛逼|牛啊|好听|好看|帅|支持|喜欢|爱了|绝了|可以啊|强")
GENERIC_ASK = re.compile(r"[？?]|吗|怎么|为什么|能不能|可不可以|请问|求|有没有|哪个")


def cookie_header():
    with open(COOKIE_FILE, "r", encoding="utf-8") as f:
        j = json.load(f)
    if isinstance(j, dict) and "cookie_info" in j:
        ck = {c["name"]: c["value"] for c in j["cookie_info"]["cookies"]}
    elif isinstance(j, dict) and "cookies" in j:
        ck = {c["name"]: c["value"] for c in j["cookies"]}
    else:
        ck = {k: str(v) for k, v in j.items()}


def http(url, referer, data=None, method=None):
    cookie, _ = cookie_header()
    headers = {"User-Agent": F.UA, "Referer": referer, "Cookie": cookie}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        method = method or "POST"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def whoami():
    d = http("https://api.bilibili.com/x/web-interface/nav", "https://www.bilibili.com/")
    dd = d.get("data") or {}
    return dd.get("uname"), str(dd.get("mid") or "")


def own_archives(ps=20):
    d = http("https://member.bilibili.com/x/web/archives?pn=1&ps=%d&coop=1&interactive=1" % ps,
             "https://member.bilibili.com/platform/upload-manager/article")
    out = []
    for a in (d.get("data") or {}).get("arc_audits") or []:
        arc = a.get("Archive") or a
        if arc.get("state_desc") == "开放浏览":
            out.append({"aid": arc.get("aid"), "bvid": arc.get("bvid"), "title": arc.get("title")})
    return out


def fetch_roots(aid, bvid, pages=1):
    out = []
    for pn in range(1, pages + 1):
        d = http("https://api.bilibili.com/x/v2/reply?oid=%d&type=1&pn=%d&ps=20&sort=0"
                 % (aid, pn), "https://www.bilibili.com/video/" + bvid)
        if d.get("code") != 0:
            break
        reps = (d.get("data") or {}).get("replies") or []
        out += reps
        if len(reps) < 20:
            break
        time.sleep(0.8)
    return out


def gen_rules(msg):
    """确定性生成：命中事实库就答，正反馈就具体回应，纯提问但不认识就说不确定。"""
    for pat, answer in FACTS:
        if re.search(pat, msg):
            return answer
    if GENERIC_POS.search(msg):
        return "谢谢，这期把控制面板和真实群聊实录拼在一起，后面还会补细节。"
    if GENERIC_ASK.search(msg):
        return "这个我还没实测过，不敢乱说；你按置顶评论里的 README 试，有问题我再补。"
    return "收到，这条我看了。"


def gen_llm(msg, title):
    base = os.environ.get("OPENAI_BASE", "https://api.deepseek.com/v1").rstrip("/")
    key = os.environ.get("OPENAI_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    model = os.environ.get("OPENAI_MODEL", "deepseek-chat")
    if not key:
        return None, "no_key"
    prompt = ("你是B站UP主本人，在回复自己视频下的评论。口吻：真人、短、具体，一句话能说完就一句话，"
              "不用「感谢支持」这类模板，不加 emoji，不自称 AI，不引导私信，不放链接。\n"
              "视频标题：%s\n评论：%s\n只输出回复正文。" % (title, msg))
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0.7, "max_tokens": 120}, ensure_ascii=False).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode("utf-8"))
        return d["choices"][0]["message"]["content"].strip(), ""
    except Exception as e:  # 网络/额度/格式问题都退回规则生成
        return None, "%s: %s" % (type(e).__name__, str(e)[:80])


def llm_is_human(msg, uname, level):
    base = os.environ.get("OPENAI_BASE", "https://api.deepseek.com/v1").rstrip("/")
    key = os.environ.get("OPENAI_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    model = os.environ.get("OPENAI_MODEL", "deepseek-chat")
    if not key:
        return None, "no_key"
    prompt = ("判断这条B站评论是真人还是AI/营销机器人，只看语言与账号特征。"
              "账号：%s（Lv%d）；评论：%s\n只输出一个词：human 或 bot。" % (uname, level, msg))
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0, "max_tokens": 8}, ensure_ascii=False).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode("utf-8"))
        out = d["choices"][0]["message"]["content"].strip().lower()
        return ("human" if "human" in out else "bot"), ""
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:80])


def init_db(path):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE IF NOT EXISTS seen (
        rpid TEXT PRIMARY KEY, aid TEXT, bvid TEXT, mid TEXT, uname TEXT, level INT,
        msg TEXT, verdict TEXT, score REAL, needs_llm INT, action TEXT,
        reply_text TEXT, ts TEXT, live INT, ok INT, err TEXT)""")
    con.commit()
    return con


def already(con, rpid):
    return con.execute("SELECT 1 FROM seen WHERE rpid=?", (str(rpid),)).fetchone() is not None


def replied_to_user_recently(con, mid, hours=24):
    row = con.execute(
        "SELECT COUNT(*) FROM seen WHERE mid=? AND action='reply' AND live=1 "
        "AND ts > datetime('now','-%d hours')" % hours, (str(mid),)).fetchone()
    return row[0] > 0


def replied_on_video_recently(con, bvid, hours=1, cap=3):
    row = con.execute(
        "SELECT COUNT(*) FROM seen WHERE bvid=? AND action='reply' AND live=1 "
        "AND ts > datetime('now','-%d hours')" % hours, (bvid,)).fetchone()
    return row[0] >= cap


def save(con, row):
    con.execute("""INSERT OR REPLACE INTO seen
        (rpid,aid,bvid,mid,uname,level,msg,verdict,score,needs_llm,action,reply_text,ts,live,ok,err)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (str(row["rpid"]), str(row["aid"]), row["bvid"], str(row["mid"]), row["uname"],
                 row["level"], row["msg"], row["verdict"], row["score"], int(row["needs_llm"]),
                 row["action"], row["reply_text"], time.strftime("%Y-%m-%d %H:%M:%S"),
                 int(row["live"]), row.get("ok"), row.get("err")))
    con.commit()


def one_pass(args, con, stat):
    videos = own_archives()
    if args.only_bv:
        videos = [v for v in videos if v["bvid"] in args.only_bv]
    lines = []
    for v in videos:
        reps = fetch_roots(v["aid"], v["bvid"], args.pages)
        for r in reps:
            m = r.get("member") or {}
            c = r.get("content") or {}
            msg = (c.get("message") or "").strip()
            if args.only_rpid and str(r["rpid"]) not in args.only_rpid:
                continue
            if not msg or (MY_MID and str(m.get("mid")) == MY_MID):
                continue
            if already(con, r["rpid"]):
                stat["dup"] += 1
                continue
            level = ((m.get("level_info") or {}).get("current_level")) or 0
            uname = m.get("uname") or ""
            acct, a_why = F.account_score(m)
            txt, t_why = F.text_score(msg, c.get("members") or [])
            total = acct + txt
            if total >= 4.8 and txt >= 0.8:
                verdict = "bot"
            elif total >= 3.0:
                verdict = "unsure"
            else:
                verdict = "human"
            needs_llm = (verdict == "human" and len(msg) >= 30
                         and not F.EMOTE.search(msg)
                         and (bool(c.get("members")) or msg.count("，") >= 2))
            is_q = bool(GENERIC_ASK.search(msg))
            low_value = (len(msg) < 6) or (not re.search(r"[\u4e00-\u9fa5a-zA-Z]", msg))

            action, reply_text, ok, err = "skip", "", None, ""
            if verdict == "human":
                if needs_llm:
                    if args.gen == "none":
                        action, err = "skip_no_model", "gen=none"
                    else:
                        verdict_by_llm, why = llm_is_human(msg, uname, level)
                        if verdict_by_llm is None:
                            action, err = "skip_no_model", why
                        elif verdict_by_llm != "human":
                            verdict, action, err = "bot_by_llm", "skip", ""
                        else:
                            action = "reply"
                elif low_value:
                    action = "skip_low_value"
                else:
                    action = "reply"

                if action == "reply":
                    if replied_to_user_recently(con, m.get("mid")):
                        action, err = "skip_user_24h", ""
                    elif replied_on_video_recently(con, v["bvid"]):
                        action, err = "skip_video_1h", ""
                    elif args.gen == "none":
                        action, err = "skip_no_model", "gen=none"
                    else:
                        reply_text = ""
                        if args.gen == "llm":
                            reply_text, why = gen_llm(msg, v["title"])
                            err = why
                        if not reply_text:
                            reply_text = gen_rules(msg)
                            if args.gen == "llm" and why:
                                err = "llm_fallback:" + why
                        if args.live:
                            try:
                                d = http("https://api.bilibili.com/x/v2/reply/add",
                                         "https://www.bilibili.com/video/" + v["bvid"],
                                         {"oid": v["aid"], "type": 1, "root": r["rpid"],
                                          "parent": r["rpid"], "message": reply_text,
                                          "csrf": cookie_header()[1]["bili_jct"]})
                                code = d.get("code")
                                ok = int(code == 0)
                                err = "" if ok else "%s %s" % (code, d.get("message"))
                                if code == 601:  # 太频繁，退避
                                    time.sleep(60)
                                stat["sent"] += 1 if ok else 0
                                stat["err"] += 0 if ok else 1
                                time.sleep(6)
                            except urllib.error.HTTPError as e:
                                ok, err = 0, "HTTP %s" % e.code
                                stat["err"] += 1

            stat[verdict if verdict in ("bot", "unsure") else
                 ("llm" if needs_llm else "human")] += 1
            if action == "reply" and not ok and args.live:
                stat["fail"] += 1

            save(con, {"rpid": r["rpid"], "aid": v["aid"], "bvid": v["bvid"], "mid": m.get("mid"),
                       "uname": uname, "level": level, "msg": msg, "verdict": verdict,
                       "score": round(total, 1), "needs_llm": needs_llm, "action": action,
                       "reply_text": reply_text, "live": args.live, "ok": ok, "err": err})
            if action != "skip":
                lines.append("## %s · %s · %s" % (v["bvid"], verdict, action))
                lines.append("- 评论：%s（%s，Lv%d）" % (msg.replace("\n", " ")[:120], uname, level))
                lines.append("- 提问：%s｜打分 %.1f（账号 %.1f / 文本 %.1f）｜%s"
                             % ("是" if is_q else "否", total, acct, txt,
                                " / ".join(a_why + t_why)))
                lines.append("- 回复：%s" % (reply_text or "（跳过：%s）" % (err or action)))
                if args.live:
                    lines.append("- 发送：%s %s" % ("成功" if ok else "失败", err))
                lines.append("")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-bv", action="append", default=[])
    ap.add_argument("--only-rpid", action="append", default=[], help="只处理指定评论 rpid（可多次）")
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--max", type=int, default=5)
    ap.add_argument("--db", default=os.path.join(HERE, "..", "out", "reply_state.db"))
    ap.add_argument("--log", default="")
    ap.add_argument("--gen", default="rules", choices=["rules", "llm", "none"])
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--cycles", type=int, default=0, help="watch 轮数，0=一直跑")
    ap.add_argument("--probe-rpid", default="", help="发送链路自检：对指定评论发一条测试回复，回读后立即删除")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    uname, mid = whoami()
    print("[%s] 已登录：%s (%s)" % (time.strftime("%H:%M:%S"), uname, mid), flush=True)
    if EXPECTED_UNAME and uname != EXPECTED_UNAME:
        raise SystemExit("账号不对：期望 %s，实际 %s —— 停止" % (EXPECTED_UNAME, uname))
    if args.live:
        if not EXPECTED_UNAME:
            raise SystemExit("live 模式必须设 BILI_EXPECTED_UNAME，先确认登的是谁的号")
        if not args.yes:
            raise SystemExit("live 模式需要 --yes")

    if args.probe_rpid:
        os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
        con = init_db(args.db)
        bv = (args.only_bv or ["BV1U1nF6PE7e"])[0]
        view = http("https://api.bilibili.com/x/web-interface/view?bvid=" + bv,
                    "https://www.bilibili.com/video/" + bv)
        aid = view["data"]["aid"]
        mark = "测试自动回复链路，马上删除（DSH 插件验证）"
        res = http("https://api.bilibili.com/x/v2/reply/add",
                   "https://www.bilibili.com/video/" + bv,
                   {"oid": aid, "type": 1, "root": args.probe_rpid,
                    "parent": args.probe_rpid, "message": mark,
                    "csrf": cookie_header()[1]["bili_jct"]})
        print("probe 发送 ->", json.dumps(res, ensure_ascii=False)[:200], flush=True)
        new_rpid = (res.get("data") or {}).get("rpid")
        time.sleep(2)
        subs = http("https://api.bilibili.com/x/v2/reply/reply?oid=%d&type=1&root=%s&pn=1&ps=20"
                    % (aid, args.probe_rpid), "https://www.bilibili.com/video/" + bv)
        found = any(str(s.get("rpid")) == str(new_rpid)
                    for s in (subs.get("data") or {}).get("replies") or [])
        print("probe 回读：挂在目标楼层 =", found, flush=True)
        dele = http("https://api.bilibili.com/x/v2/reply/del",
                    "https://www.bilibili.com/video/" + bv,
                    {"oid": aid, "type": 1, "rpid": new_rpid,
                     "csrf": cookie_header()[1]["bili_jct"]})
        print("probe 删除 ->", json.dumps(dele, ensure_ascii=False)[:160], flush=True)
        time.sleep(3)
        subs2 = http("https://api.bilibili.com/x/v2/reply/reply?oid=%d&type=1&root=%s&pn=1&ps=20"
                     % (aid, args.probe_rpid), "https://www.bilibili.com/video/" + bv)
        still = any(str(s.get("rpid")) == str(new_rpid)
                    for s in (subs2.get("data") or {}).get("replies") or [])
        print("probe 删除后仍在 =", still, flush=True)
        save(con, {"rpid": new_rpid, "aid": aid, "bvid": bv, "mid": "probe",
                   "uname": "self-test", "level": 0, "msg": mark, "verdict": "self_test",
                   "score": 0, "needs_llm": 0,
                   "action": "probe_ok" if (found and not still) else "probe_incomplete",
                   "reply_text": mark, "live": 1, "ok": 1 if found else 0,
                   "err": "deleted_verify=%s" % (not still)})
        return
    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    con = init_db(args.db)

    cycle = 0
    while True:
        cycle += 1
        stat = {"bot": 0, "unsure": 0, "human": 0, "llm": 0, "dup": 0,
                "sent": 0, "err": 0, "fail": 0, "low": 0}
        lines = one_pass(args, con, stat)
        head = ["# 自动回复 %s 第 %d 轮" % ("LIVE" if args.live else "dry-run", cycle),
                "", "- 时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
                "- 模式：%s / gen=%s / 账号 %s(%s)" % (
                    "LIVE" if args.live else "dry-run", args.gen, uname, mid), ""]
        tail = ["## 汇总",
                "- 可回 %d｜送 LLM %d｜bot %d｜unsure %d｜重复 %d｜发出 %d｜失败 %d"
                % (stat["human"], stat["llm"], stat["bot"], stat["unsure"],
                   stat["dup"], stat["sent"], stat["err"])]
        text = "\n".join(head + lines + tail)
        print(text, flush=True)
        log_path = args.log or os.path.join(HERE, "..", "out",
                                            "reply_%s_%s.md" % ("live" if args.live else "dryrun",
                                                                time.strftime("%Y%m%d")))
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(text + "\n\n")
        print("log ->", os.path.abspath(log_path), flush=True)

        if not args.watch:
            break
        if args.cycles and cycle >= args.cycles:
            break
        time.sleep(max(10, args.interval))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()