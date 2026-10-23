# -*- coding: utf-8 -*-
"""打印指定桶的样本，便于人工标注：python dump_bucket.py <jsonl> dsh-human 30"""
import json
import sys
from collections import Counter

DSH = {"BV1VJnA6SEV4", "BV1U1nF6PE7e", "BV1tTi5BrETr", "BV1Uyp26MELu", "BV1tknn6oESZ",
       "BV1Vwn76tEtq", "BV15NJi65E1p", "BV1yVJz6vEe3", "BV1uYnj67E42", "BV1GcnL6nEvR",
       "BV17Mn56dECu", "BV1evnV6EE9j", "BV1Qdng6TE5o", "BV1EoJv6SELk", "BV18Ubv6tEWN",
       "BV1JTn564EYV", "BV1zHJq6HEsn", "BV1fBng6UEZA"}

path, bucket = sys.argv[1], sys.argv[2]
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 30
rows = [json.loads(ln) for ln in open(path, encoding="utf-8") if ln.strip()]

if bucket == "dsh-human":
    sel = [r for r in rows if r["bv"] in DSH and r["verdict"] == "human"]
elif bucket == "normal-human":
    sel = [r for r in rows if r["bv"] not in DSH and r["verdict"] == "human"]
elif bucket == "dsh-bot":
    sel = [r for r in rows if r["bv"] in DSH and r["verdict"] == "bot"]
elif bucket == "normal-unsure":
    sel = [r for r in rows if r["bv"] not in DSH and r["verdict"] == "unsure"]
else:
    raise SystemExit("unknown bucket")

print("bucket=%s  n=%d\n" % (bucket, len(sel)))
for i, r in enumerate(sel[:limit], 1):
    print("%2d. Lv%d %-16s %s" % (i, r["level"], r["uname"][:16],
                                  r["msg"].replace("\n", " ")[:100]))
