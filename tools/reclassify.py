# -*- coding: utf-8 -*-
"""在已采语料上重新打分（不联网），并按分组输出统计与误判样本。

用法：python reclassify.py <corpus.jsonl> [out_csv]
"""
import csv
import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bili_bot_filter as F  # noqa: E402

DSH = {"BV1VJnA6SEV4", "BV1U1nF6PE7e", "BV1tTi5BrETr", "BV1Uyp26MELu", "BV1tknn6oESZ",
       "BV1Vwn76tEtq", "BV15NJi65E1p", "BV1yVJz6vEe3", "BV1uYnj67E42", "BV1GcnL6nEvR",
       "BV17Mn56dECu", "BV1evnV6EE9j", "BV1Qdng6TE5o", "BV1EoJv6SELk", "BV18Ubv6tEWN",
       "BV1JTn564EYV", "BV1zHJq6HEsn", "BV1fBng6UEZA"}

path = sys.argv[1]
out_csv = sys.argv[2] if len(sys.argv) > 2 else ""
rows = [json.loads(ln) for ln in open(path, encoding="utf-8") if ln.strip()]


def member_from_row(r):
    return {
        "uname": r.get("uname", ""),
        "level_info": {"current_level": r.get("level", 0)},
        "sign": "" if r.get("sign_empty") else "x",
        "pendant": {"pid": 1 if r.get("has_pendant") else 0},
        "nameplate": {"nid": 1 if r.get("has_nameplate") else 0},
        "vip": {"vipStatus": r.get("vip", 0)},
        "official_verify": {"type": r.get("official", -1)},
        "is_senior_member": r.get("senior", 0),
    }


per_mid = {}
for r in rows:
    per_mid.setdefault(r["mid"], set()).add(r["bv"])
msg_counter = Counter()
for r in rows:
    if len(r["msg"]) >= 16:
        msg_counter[r["msg"]] += 1

for r in rows:
    acct, a_why = F.account_score(member_from_row(r))
    txt, t_why = F.text_score(r["msg"], r.get("at_list") or [])
    nvid = len(per_mid.get(r["mid"], set()))
    cross = 1.8 if nvid >= 3 else (1.2 if nvid == 2 else 0.0)
    c_why = ["出现在%d条视频" % nvid] if cross else []
    dup = msg_counter.get(r["msg"], 0)
    if dup >= 3:
        txt += 1.0
        t_why.append("长句同文x%d" % dup)
    total = acct + txt + cross
    if total >= 4.8 and (txt >= 0.8 or cross >= 1.2):
        verdict = "bot"
    elif total >= 3.0:
        verdict = "unsure"
    else:
        verdict = "human"
    needs_llm = (verdict == "human" and len(r["msg"]) >= 30
                 and not F.EMOTE.search(r["msg"])
                 and (bool(r.get("at_list")) or r["msg"].count("，") >= 2))
    r.update({"needs_llm": needs_llm})
    r.update({"acct": round(acct, 1), "text": round(txt, 1), "cross": round(cross, 1),
              "total": round(total, 1), "verdict": verdict,
              "why": " / ".join(a_why + t_why + c_why)})

for grp, name in ((True, "DSH 圈"), (False, "热门区")):
    sel = [r for r in rows if (r["bv"] in DSH) == grp]
    c = Counter(r["verdict"] for r in sel)
    print("%s  n=%-5d bot=%-4d unsure=%-4d human=%d" %
          (name, len(sel), c["bot"], c["unsure"], c["human"]))

print("\n--- 热门区判 bot 的（疑似误判重点看） ---")
sel = [r for r in rows if r["bv"] not in DSH and r["verdict"] == "bot"]
for r in sel[:15]:
    print("Lv%d %-14s %s" % (r["level"], r["uname"][:14], r["msg"].replace("\n", " ")[:62]))
    print("    %.1f (a=%.1f t=%.1f c=%.1f) %s" % (r["total"], r["acct"], r["text"],
                                                  r["cross"], r["why"][:70]))

print("\n--- 热门区判 unsure 的（前 15） ---")
sel = [r for r in rows if r["bv"] not in DSH and r["verdict"] == "unsure"]
for r in sel[:15]:
    print("Lv%d %-14s %s | %.1f %s" % (r["level"], r["uname"][:14],
                                       r["msg"].replace("\n", " ")[:50], r["total"],
                                       r["why"][:60]))

print("\n--- DSH 圈判 bot 的（前 12） ---")
sel = [r for r in rows if r["bv"] in DSH and r["verdict"] == "bot"]
for r in sel[:12]:
    print("Lv%d %-16s %s" % (r["level"], r["uname"][:16], r["msg"].replace("\n", " ")[:58]))

print("\n--- DSH 圈判 human 的（看漏判，前 12） ---")
sel = [r for r in rows if r["bv"] in DSH and r["verdict"] == "human"]
for r in sel[:12]:
    print("Lv%d %-16s %s" % (r["level"], r["uname"][:16], r["msg"].replace("\n", " ")[:58]))

if out_csv:
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rpid", "bv", "is_sub", "mid", "uname", "level", "msg",
                    "acct", "text", "cross", "total", "verdict", "why", "needs_llm", "label"])
        for r in sorted(rows, key=lambda x: -x["total"]):
            w.writerow([r["rpid"], r["bv"], int(r.get("is_sub", 0)), r["mid"], r["uname"],
                        r["level"], r["msg"], r["acct"], r["text"], r["cross"],
                        r["total"], r["verdict"], r["why"], ""])
    print("needs_llm:", sum(1 for r in rows if r.get("needs_llm")))
    jl = out_csv[:-4] + ".jsonl" if out_csv.endswith(".csv") else out_csv + ".jsonl"
    with open(jl, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("jsonl ->", jl)
    print("csv ->", out_csv)