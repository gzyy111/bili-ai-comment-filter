# -*- coding: utf-8 -*-
"""采集评论语料（含楼中楼）并打初步判定，产出 CSV 供人工标注。

用法：
    python collect_corpus.py --bvs-file bvs_all.txt --pages 1 --sub-roots 8 \
        --cookie gui --out-jsonl <tmp>/corpus.jsonl --out-csv <tmp>/corpus.csv
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bili_bot_filter as F  # noqa: E402

UA = F.UA
REF = F.REF
COOKIE = ""


def load_cookie(source, cookie_file=""):
    """cookie 来源：none（游客态）/ env（环境变量 BILI_COOKIE）/ file（读 JSON 文件）。

    文件兼容三种结构：biliup-app 的 {"cookie_info": {"cookies": [...]}}、
    {"cookies": [...]}、以及扁平的 {name: value}。
    """
    if source == "none":
        return ""
    if source == "env":
        return os.environ.get("BILI_COOKIE", "").strip()
    path = cookie_file or os.environ.get("BILI_COOKIE_FILE", "cookies.json")
    with open(path, "r", encoding="utf-8") as f:
        j = json.load(f)
    if isinstance(j, dict) and "cookie_info" in j:
        ck = {c["name"]: c["value"] for c in j["cookie_info"]["cookies"]}
    elif isinstance(j, dict) and "cookies" in j:
        ck = {c["name"]: c["value"] for c in j["cookies"]}
    else:
        ck = {k: str(v) for k, v in j.items()}
    return "; ".join("%s=%s" % (k, v) for k, v in ck.items())


def fetch(url, referer):
    headers = {"User-Agent": UA, "Referer": referer}
    if COOKIE:
        headers["Cookie"] = COOKIE
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_roots(aid, bv, pages):
    out = []
    for pn in range(1, pages + 1):
        d = fetch("https://api.bilibili.com/x/v2/reply?oid=%d&type=1&pn=%d&ps=20&sort=2"
                  % (aid, pn), "https://www.bilibili.com/video/" + bv)
        if d.get("code") != 0:
            break
        reps = (d.get("data") or {}).get("replies") or []
        out += reps
        if len(reps) < 20:
            break
        time.sleep(0.7)
    return out


def fetch_subs(aid, bv, rpid):
    d = fetch("https://api.bilibili.com/x/v2/reply/reply?oid=%d&type=1&root=%d&pn=1&ps=10"
              % (aid, rpid), "https://www.bilibili.com/video/" + bv)
    if d.get("code") != 0:
        return []
    return (d.get("data") or {}).get("replies") or []


def to_record(r, bv, aid, root=None):
    m = r.get("member") or {}
    c = r.get("content") or {}
    return {
        "bv": bv, "aid": aid,
        "rpid": r.get("rpid"),
        "root": r.get("root") or root or 0,
        "parent": r.get("parent") or 0,
        "is_sub": bool(root),
        "mid": m.get("mid") or "",
        "uname": m.get("uname") or "",
        "level": ((m.get("level_info") or {}).get("current_level")) or 0,
        "sign_empty": not (m.get("sign") or "").strip(),
        "has_pendant": ((m.get("pendant") or {}).get("pid") or 0) > 0,
        "has_nameplate": ((m.get("nameplate") or {}).get("nid") or 0) > 0,
        "vip": ((m.get("vip") or {}).get("vipStatus")) or 0,
        "official": ((m.get("official_verify") or {}).get("type", -1)),
        "senior": (m.get("is_senior_member") or 0),
        "ctime": r.get("ctime") or 0,
        "like": r.get("like") or 0,
        "rcount": r.get("rcount") or 0,
        "msg": (c.get("message") or "").strip(),
        "at_list": c.get("members") or [],
        "_member": m,
    }


def main():
    global COOKIE
    ap = argparse.ArgumentParser()
    ap.add_argument("--bvs-file", required=True)
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--sub-roots", type=int, default=8)
    ap.add_argument("--cookie", default="file", choices=["none", "env", "file"])
    ap.add_argument("--cookie-file", default="",
                    help="cookie JSON 路径；默认读环境变量 BILI_COOKIE_FILE 或 ./cookies.json")
    ap.add_argument("--out-jsonl", required=True)
    ap.add_argument("--out-csv", required=True)
    args = ap.parse_args()
    COOKIE = load_cookie(args.cookie, args.cookie_file)

    with open(args.bvs_file, "r", encoding="utf-8") as f:
        bvs = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]

    recs = []
    for bv in bvs:
        try:
            d = fetch("https://api.bilibili.com/x/web-interface/view?bvid=" + bv,
                      "https://www.bilibili.com/video/" + bv)
        except urllib.error.HTTPError:
            print("skip", bv, "(view http)", file=sys.stderr)
            continue
        if d.get("code") != 0:
            print("skip", bv, "(view code)", file=sys.stderr)
            continue
        aid = d["data"]["aid"]
        title = d["data"].get("title") or ""
        roots = fetch_roots(aid, bv, args.pages)
        recs += [to_record(r, bv, aid) for r in roots]
        subs_done = 0
        for r in roots:
            if (r.get("rcount") or 0) <= 0 or subs_done >= args.sub_roots:
                continue
            subs = fetch_subs(aid, bv, r["rpid"])
            recs += [to_record(s, bv, aid, root=r["rpid"]) for s in subs]
            subs_done += 1
            time.sleep(0.7)
        print("%s  %s  roots=%d subroots=%d" % (bv, title[:24], len(roots), subs_done),
              file=sys.stderr)
        time.sleep(0.9)

    per_mid = {}
    for r in recs:
        per_mid.setdefault(r["mid"], set()).add(r["bv"])
    dup_msg = {}
    for r in recs:
        dup_msg[r["msg"]] = dup_msg.get(r["msg"], 0) + 1

    for r in recs:
        acct, a_why = F.account_score(r["_member"])
        txt, t_why = F.text_score(r["msg"], r["at_list"])
        nvid = len(per_mid.get(r["mid"], set()))
        cross = 1.8 if nvid >= 3 else (1.2 if nvid == 2 else 0.0)
        c_why = []
        if cross:
            c_why.append("出现在%d条视频" % nvid)
        if dup_msg.get(r["msg"], 0) >= 2:
            txt += 1.5
            t_why.append("同文复读x%d" % dup_msg[r["msg"]])
        total = acct + txt + cross
        if total >= 4.8 and (txt >= 0.8 or cross >= 1.2):
            verdict = "bot"
        elif total >= 3.0:
            verdict = "unsure"
        else:
            verdict = "human"
        r.update({"acct": round(acct, 1), "text": round(txt, 1), "cross": round(cross, 1),
                  "total": round(total, 1), "verdict": verdict,
                  "why": " / ".join(a_why + t_why + c_why)})

    with open(args.out_jsonl, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps({k: v for k, v in r.items() if k != "_member"},
                               ensure_ascii=False) + "\n")

    with open(args.out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rpid", "bv", "is_sub", "mid", "uname", "level", "sign_empty",
                    "vip", "senior", "like", "rcount", "msg", "acct", "text", "cross",
                    "total", "auto_verdict", "why", "label"])
        for r in recs:
            w.writerow([r["rpid"], r["bv"], int(r["is_sub"]), r["mid"], r["uname"],
                        r["level"], int(r["sign_empty"]), r["vip"], r["senior"],
                        r["like"], r["rcount"], r["msg"], r["acct"], r["text"],
                        r["cross"], r["total"], r["verdict"], r["why"], ""])

    stat = {"bot": 0, "unsure": 0, "human": 0}
    for r in recs:
        stat[r["verdict"]] += 1
    print("\nrecords=%d  bot=%d unsure=%d human=%d  videos=%d  uniq_mid=%d"
          % (len(recs), stat["bot"], stat["unsure"], stat["human"], len(bvs),
             len({r["mid"] for r in recs})))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
