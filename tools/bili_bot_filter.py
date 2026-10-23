# -*- coding: utf-8 -*-
"""B站评论「真人 / AI号」判定器（规则版，只读，不回复）。

用法：
    python bili_bot_filter.py --bvs-file bvs.txt --pages 2 --out out/report.md
    python bili_bot_filter.py --bv BV1U1nF6PE7e --bv BV1VJnA6SEV4

判定分三层：
  1) 账号画像分（等级/签名/挂件/名牌/会员/认证/硬核）
  2) 文本特征分（自称拟物、模板开场、@ 无细节、标点过规整、跨作者复读）
  3) 跨视频行为分（同一 mid 出现在几条被扫视频里）
输出 human / unsure / bot，unsure 按「不回」处理。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
REF = "https://www.bilibili.com/"
# 可选：带上登录 cookie 才能拿到满 20 条/页（游客态每页只回 3 条）。
# 用法：$env:BILI_COOKIE="SESSDATA=...; bili_jct=...; buvid3=..."
COOKIE = os.environ.get("BILI_COOKIE", "").strip()

SELF_REF = re.compile(r"本(鱼|喵|汪|UP|up|人|bot|Bot)|作为(一个)?(AI|人工智能)|"
                      r"我是(机器人|AI|人工智)|同类(嘛|呀|呢)|同为(机器人|AI)")
TEMPLATE = re.compile(r"这个我熟|有一说一|讲真|说实话|不得不说|懂的都懂|"
                      r"总而言之|先说结论|属实是|简单来说")
NO_DETAIL = re.compile(r"\d")
CUTE_TAIL = re.compile(r"(还有很长的路要走|一起加油|期待下一期|希望你|加油[！!]?|"
                       r"这条我记下了|我一条条回)")
PERSONA = re.compile(r"主人|喵呜|本喵|奴家|咱家")
OWNER_CALL = re.compile(r"^(主人|@[^\s]+\s*主人)|主人[，,～~]")
BOT_NAME = re.compile(r"大肥鱼|肥鱼|鲸鱼娘|小鲸鱼|鲸鲸|鱼鱼|的鱼|鲸$")
HUMAN_CHAT = re.compile(r"\[[^\]]{1,6}\]|[笑哭狗头doge]|啥都|看不懂|听不懂|真人|活人")
EMOTE = re.compile(r"\[[^\]]{1,8}\]")


def get_json(url, referer=REF):
    headers = {"User-Agent": UA, "Referer": referer}
    if COOKIE:
        headers["Cookie"] = COOKIE
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def bvid_to_aid(bv):
    d = get_json("https://api.bilibili.com/x/web-interface/view?bvid=" + bv,
                 "https://www.bilibili.com/video/" + bv)
    if d.get("code") != 0:
        return None, None
    v = d["data"]
    return v.get("aid"), v.get("title")


def fetch_comments(aid, bv, pages=1):
    out = []
    for pn in range(1, pages + 1):
        url = ("https://api.bilibili.com/x/v2/reply?oid=%d&type=1&pn=%d&ps=20&sort=2"
               % (aid, pn))
        try:
            d = get_json(url, "https://www.bilibili.com/video/" + bv)
        except urllib.error.HTTPError as e:
            print("  ! page %d HTTP %s" % (pn, e.code), file=sys.stderr)
            break
        if d.get("code") != 0:
            print("  ! page %d code %s" % (pn, d.get("code")), file=sys.stderr)
            break
        reps = (d.get("data") or {}).get("replies") or []
        if not reps:
            break
        for r in reps:
            out.append(r)
        if len(reps) < 20:
            break
        time.sleep(0.8)
    return out


def account_score(m):
    lv = ((m.get("level_info") or {}).get("current_level")) or 0
    s = 0.0
    reasons = []
    if lv <= 1:
        s += 3.0
        reasons.append("Lv%d" % lv)
    elif lv <= 2:
        s += 2.0
        reasons.append("Lv%d" % lv)
    elif lv <= 3:
        s += 0.5
    if not (m.get("sign") or "").strip():
        s += 1.0
        reasons.append("空签名")
    if ((m.get("pendant") or {}).get("pid") or 0) == 0:
        s += 0.3
    if ((m.get("nameplate") or {}).get("nid") or 0) == 0:
        s += 0.3
    if ((m.get("vip") or {}).get("vipStatus") or 0) == 0:
        s += 0.3
    if ((m.get("official_verify") or {}).get("type", -1)) == -1:
        s += 0.3
    if (m.get("is_senior_member") or 0) == 0:
        s += 0.3
    uname = m.get("uname") or ""
    if BOT_NAME.search(uname):
        s += 0.8
        reasons.append("bot命名家族:%s" % BOT_NAME.search(uname).group(0))
    if not reasons:
        reasons.append("账号有装饰")
    else:
        reasons.append("无挂件/名牌/会员/认证")
    return s, reasons


def text_score(msg, members):
    s = 0.0
    reasons = []
    L = len(msg)
    has_emote = bool(EMOTE.search(msg))
    if SELF_REF.search(msg):
        s += 2.0
        reasons.append("自称拟物:%s" % SELF_REF.search(msg).group(0))
    tpl = TEMPLATE.findall(msg)
    if tpl:
        s += min(1.2, 0.4 * len(tpl))
        reasons.append("模板开场:%s" % "、".join(tpl[:3]))
    # @人且是长句、无表情：真人的短回复大多是 @+一两个词，不能当成 bot 依据
    if members and L >= 25 and not has_emote:
        s += 0.6
        reasons.append("@人+长句无表情")
    if 40 <= L <= 160 and msg.endswith("。") and not re.search(r"[哈嘿嗯啊呜233]|\[", msg):
        s += 0.4
        reasons.append("标点过规整")
    if CUTE_TAIL.search(msg):
        s += 0.5
        reasons.append("套话收尾")
    if PERSONA.search(msg):
        s += 1.5
        reasons.append("人设口吻:%s" % PERSONA.search(msg).group(0))
    if OWNER_CALL.search(msg):
        s += 1.0
        reasons.append("开头喊主人")
    # 真人信号（短评 / 表情包短评）
    if L <= 10:
        s -= 1.5
        reasons.append("短评(-)")
    elif has_emote and L <= 40:
        s -= 1.0
        reasons.append("表情短评(-)")
    return max(s, -1.5), reasons


def members_has_at(members):
    return bool(members)


def members_has_at(members):
    return bool(members)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bv", action="append", default=[])
    ap.add_argument("--bvs-file", default="")
    ap.add_argument("--pages", type=int, default=2)
    ap.add_argument("--out", default="out/report.md")
    args = ap.parse_args()

    bvs = list(args.bv)
    if args.bvs_file:
        with open(args.bvs_file, "r", encoding="utf-8") as f:
            bvs += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    bvs = [b.strip() for b in bvs if b.strip()]
    if not bvs:
        raise SystemExit("no BV given")

    rows = []
    per_mid_videos = {}
    msg_counter = {}

    for bv in bvs:
        aid, title = bvid_to_aid(bv)
        if not aid:
            print("skip %s (no aid)" % bv, file=sys.stderr)
            continue
        reps = fetch_comments(aid, bv, args.pages)
        print("%s  %s  comments=%d" % (bv, (title or "")[:28], len(reps)), file=sys.stderr)
        for r in reps:
            m = r.get("member") or {}
            c = r.get("content") or {}
            msg = (c.get("message") or "").strip()
            if not msg:
                continue
            mid = m.get("mid") or ""
            per_mid_videos.setdefault(mid, set()).add(bv)
            msg_counter[msg] = msg_counter.get(msg, 0) + 1
            rows.append({
                "bv": bv, "aid": aid, "mid": mid,
                "uname": m.get("uname") or "",
                "level": ((m.get("level_info") or {}).get("current_level")) or 0,
                "sign": (m.get("sign") or ""),
                "vip": ((m.get("vip") or {}).get("vipStatus")) or 0,
                "like": r.get("like") or 0,
                "rcount": r.get("rcount") or 0,
                "ctime": r.get("ctime") or 0,
                "msg": msg,
                "members": c.get("members") or [],
                "rpid": r.get("rpid"), "member": m,
            })
        time.sleep(0.8)

    # 判定
    for row in rows:
        acct, a_reasons = account_score(row["member"] or {})
        txt, t_reasons = text_score(row["msg"], row["members"])
        nvid = len(per_mid_videos.get(row["mid"], set()))
        cross = 0.0
        c_reasons = []
        if nvid >= 3:
            cross = 1.8
            c_reasons.append("出现在%d条视频" % nvid)
        elif nvid == 2:
            cross = 1.2
            c_reasons.append("出现在2条视频")
        dup = msg_counter.get(row["msg"], 0)
        if dup >= 2:
            txt += 1.5
            t_reasons.append("同文复读x%d" % dup)

        total = acct + txt + cross
        if total >= 4.8 and (txt >= 0.8 or cross >= 1.2):
            verdict = "bot"
        elif total >= 3.0:
            verdict = "unsure"
        else:
            verdict = "human"
        row.update({"acct": round(acct, 1), "text": round(txt, 1), "cross": round(cross, 1),
                    "total": round(total, 1), "verdict": verdict,
                    "why": " / ".join(a_reasons + t_reasons + c_reasons)})

    rows.sort(key=lambda r: -r["total"])
    order = {"bot": 0, "unsure": 1, "human": 2}
    rows.sort(key=lambda r: (order[r["verdict"]], -r["total"]))

    stat = {"bot": 0, "unsure": 0, "human": 0}
    for r in rows:
        stat[r["verdict"]] += 1

    lines = []
    lines.append("# B站评论 AI 号判定（规则版）\n")
    lines.append("- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("- 扫描视频：%d 条；评论：%d 条；去重账号：%d 个" %
                 (len(bvs), len(rows), len({r["mid"] for r in rows})))
    lines.append("- 判定：bot **%d** / unsure **%d** / human **%d**（unsure 按不回处理）\n"
                 % (stat["bot"], stat["unsure"], stat["human"]))
    lines.append("| # | 判定 | 分 | 账号 | 等级 | 评论 | 依据 |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        msg = r["msg"].replace("\n", " ").replace("|", "/")[:46]
        lines.append("| %d | **%s** | %.1f | %s(%s) | %d | %s | %s |"
                     % (i, r["verdict"], r["total"], r["uname"].replace("|", "/"),
                        r["mid"], r["level"], msg, r["why"]))
    lines.append("")
    lines.append("> 规则版无 ground truth，只能当「初筛 + 人工核对」用；"
                 "unsure 是刻意保留的中间档，宁可漏回不可错回。")
    text = "\n".join(lines)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print("\nwritten ->", args.out, file=sys.stderr)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()