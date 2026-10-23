# -*- coding: utf-8 -*-
"""一次性的发送链路验证：在指定评论下发一条测试回复，回读确认挂在正确楼层，然后立即删除。

用法：python live_test_send.py --bv BV1U1nF6PE7e
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

COOKIE_FILE = os.environ.get("BILI_COOKIE_FILE", "cookies.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
MARK = "测试自动回复链路，马上删除（DSH 插件验证）"


def cookies():
    with open(COOKIE_FILE, "r", encoding="utf-8") as f:
        j = json.load(f)
    if isinstance(j, dict) and "cookie_info" in j:
        return {c["name"]: c["value"] for c in j["cookie_info"]["cookies"]}
    if isinstance(j, dict) and "cookies" in j:
        return {c["name"]: c["value"] for c in j["cookies"]}
    return {k: str(v) for k, v in j.items()}


def call(url, data=None, referer="https://www.bilibili.com/"):
    ck = cookies()
    hdr = {"User-Agent": UA, "Referer": referer,
           "Cookie": "; ".join("%s=%s" % (k, v) for k, v in ck.items())}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        hdr["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=hdr,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"code": e.code, "message": e.read().decode("utf-8", "ignore")[:120]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bv", default="BV1U1nF6PE7e")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "out", "live_test.md"))
    args = ap.parse_args()
    referer = "https://www.bilibili.com/video/" + args.bv
    lines = ["# 发送链路验证（回复某条评论）", "", "- 时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
             "- 稿件：%s" % args.bv, ""]

    nav = call("https://api.bilibili.com/x/web-interface/nav", referer=referer)
    dd = nav.get("data") or {}
    print("账号：%s (%s)" % (dd.get("uname"), dd.get("mid")))
    lines.append("- 账号：%s (%s)" % (dd.get("uname"), dd.get("mid")))
    expect = os.environ.get("BILI_EXPECTED_UNAME", "").strip()
    if expect and dd.get("uname") != expect:
        raise SystemExit("账号不对：期望 %s，实际 %s，中止" % (expect, dd.get("uname")))

    view = call("https://api.bilibili.com/x/web-interface/view?bvid=" + args.bv, referer=referer)
    aid = view["data"]["aid"]
    print("aid =", aid)

    roots = call("https://api.bilibili.com/x/v2/reply?oid=%d&type=1&pn=1&ps=20&sort=0" % aid,
                 referer=referer).get("data", {}).get("replies") or []
    if not roots:
        raise SystemExit("没有可回复的评论")
    # 选一条机器人评论做靶子，避免打扰真人
    target = roots[-1]
    for r in roots:
        name = ((r.get("member") or {}).get("uname") or "")
        msg = ((r.get("content") or {}).get("message") or "")
        if "大肥鱼" in name or "鲸" in name:
            target = r
            break
    t_mid = (target.get("member") or {}).get("mid")
    t_name = (target.get("member") or {}).get("uname")
    t_msg = (target.get("content") or {}).get("message") or ""
    t_rpid = target["rpid"]
    print("靶子评论：%s(%s) rpid=%s  %r" % (t_name, t_mid, t_rpid, t_msg[:40]))
    lines += ["## 靶子评论", "- %s (%s) · rpid `%s`" % (t_name, t_mid, t_rpid),
              "- 内容：%s" % t_msg.replace("\n", " ")[:120], ""]

    ck = cookies()
    res = call("https://api.bilibili.com/x/v2/reply/add",
               {"oid": aid, "type": 1, "root": t_rpid, "parent": t_rpid,
                "message": MARK, "csrf": ck["bili_jct"]}, referer=referer)
    print("发表结果：", json.dumps(res, ensure_ascii=False)[:220])
    lines += ["## 发表", "```json", json.dumps(res, ensure_ascii=False)[:400], "```", ""]
    if res.get("code") != 0:
        raise SystemExit("发表失败")
    new_rpid = (res.get("data") or {}).get("rpid")
    new_root = (res.get("data") or {}).get("root")
    new_parent = (res.get("data") or {}).get("parent")
    print("新回复 rpid=%s root=%s parent=%s" % (new_rpid, new_root, new_parent))

    time.sleep(2)
    subs = call("https://api.bilibili.com/x/v2/reply/reply?oid=%d&type=1&root=%s&pn=1&ps=20"
                % (aid, t_rpid), referer=referer)
    found = None
    for s in (subs.get("data") or {}).get("replies") or []:
        if str(s.get("rpid")) == str(new_rpid):
            found = s
            break
    print("回读楼中楼：找到自己回复 =", bool(found))
    lines += ["## 回读（楼中楼）",
              "- 在 root `%s` 下找到新回复：**%s**" % (t_rpid, "是" if found else "否")]
    if found:
        lines.append("- 该楼 root=`%s` parent=`%s` mid=`%s`" %
                     (found.get("root"), found.get("parent"), (found.get("member") or {}).get("mid")))
        lines.append("- 内容：%s" % ((found.get("content") or {}).get("message") or ""))
    lines.append("")

    # 删除
    for url in ("https://api.bilibili.com/x/v2/reply/del",
                "https://api.bilibili.com/x/v2/reply/action"):
        payload = {"oid": aid, "type": 1, "rpid": new_rpid, "csrf": ck["bili_jct"]}
        if "action" in url:
            payload["action"] = 0
        d = call(url, payload, referer=referer)
        print("删除尝试 %s -> %s" % (url.rsplit("/", 1)[-1], json.dumps(d, ensure_ascii=False)[:160]))
        lines.append("- 删除尝试 `%s`：%s" % (url, json.dumps(d, ensure_ascii=False)[:160]))
        if d.get("code") == 0:
            break
        time.sleep(1)

    time.sleep(3)
    subs2 = call("https://api.bilibili.com/x/v2/reply/reply?oid=%d&type=1&root=%s&pn=1&ps=20"
                 % (aid, t_rpid), referer=referer)
    still = any(str(s.get("rpid")) == str(new_rpid)
                for s in (subs2.get("data") or {}).get("replies") or [])
    print("删除后是否仍在 =", still)
    lines += ["## 回读（删除后）", "- 该回复是否还在楼中楼：**%s**" % ("在" if still else "已不在"), ""]

    text = "\n".join(lines)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print("\nreport ->", args.out)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
