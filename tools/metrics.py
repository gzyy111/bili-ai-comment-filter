# -*- coding: utf-8 -*-
"""在人工标注样本上算判定器的精确率/召回率。

用法：python metrics.py <corpus_v3.jsonl> <labels_sample.json>
"""
import json
import sys

DSH = {"BV1VJnA6SEV4", "BV1U1nF6PE7e", "BV1tTi5BrETr", "BV1Uyp26MELu", "BV1tknn6oESZ",
       "BV1Vwn76tEtq", "BV15NJi65E1p", "BV1yVJz6vEe3", "BV1uYnj67E42", "BV1GcnL6nEvR",
       "BV17Mn56dECu", "BV1evnV6EE9j", "BV1Qdng6TE5o", "BV1EoJv6SELk", "BV18Ubv6tEWN",
       "BV1JTn564EYV", "BV1zHJq6HEsn", "BV1fBng6UEZA"}

jl, lab_path = sys.argv[1], sys.argv[2]
rows = [json.loads(ln) for ln in open(jl, encoding="utf-8") if ln.strip()]
labels = json.load(open(lab_path, encoding="utf-8"))

buckets = {
    "dsh-human": [r for r in rows if r["bv"] in DSH and r["verdict"] == "human"],
    "normal-human": [r for r in rows if r["bv"] not in DSH and r["verdict"] == "human"],
}

TP = FP = FN = TN = 0
leaks = []
for name, sel in buckets.items():
    lab = labels.get(name) or {}
    for i, r in enumerate(sel[:30], 1):
        truth = lab.get(str(i), "human")
        predicted_reply = (r["verdict"] == "human")
        if truth == "human" and predicted_reply:
            TP += 1
        elif truth == "bot" and predicted_reply:
            FP += 1
            leaks.append((name, i, r))
        elif truth == "human" and not predicted_reply:
            FN += 1
        else:
            TN += 1

n = TP + FP + FN + TN
prec = TP / (TP + FP) if TP + FP else 0
rec = TP / (TP + FN) if TP + FN else 0
print("标注样本 n=%d  TP=%d FP=%d FN=%d TN=%d" % (n, TP, FP, FN, TN))
print("「回真人」精确率 = %.1f%%   召回率 = %.1f%%" % (prec * 100, rec * 100))
print("\n漏过的 bot（会被回复）：")
for name, i, r in leaks:
    print("  %s #%d Lv%d %s | %s" % (name, i, r["level"], r["uname"],
                                     r["msg"].replace("\n", " ")[:70]))
    print("      needs_llm=%s score=%.1f" % (r.get("needs_llm"), r["total"]))

if leaks:
    cov = sum(1 for _, _, r in leaks if r.get("needs_llm"))
    print("\n其中被 needs_llm 标记（可送 LLM 复核）= %d/%d" % (cov, len(leaks)))
