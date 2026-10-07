import os, shutil, sys, json
from datetime import date
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)

from contentmaster import clusters, posts, tracking_review

# --- experiment 輪替:照 cluster 編號,不照月份 ---
names = {c: [clusters.WEEKDAYS[d] for d in clusters.experiment_weekdays(c)] for c in range(1, 9)}
assert names[1] == ["Mon", "Thu"] and names[2] == ["Tue", "Fri"]
assert names[3] == ["Wed", "Sat"] and names[4] == ["Thu", "Sun"]
assert names[8] == names[1]
for start in (1, 5, 30):
    counts = [0] * 7
    for c in range(start, start + 7):
        for d in clusters.experiment_weekdays(c):
            counts[d] += 1
    assert counts == [2] * 7, (start, counts)
assert clusters.experiment_weekdays(0) == ()
print("1. 每週 2 篇 experiment;照 cluster 編號輪替;任何連續 7 個 cluster,每個星期幾剛好當 2 次 ✅")

# --- 一週一個 cluster,從週一開始 ---
assert clusters.week_start_after(date(2026, 10, 6)) == date(2026, 10, 12)   # 週二 -> 下週一
assert clusters.week_start_after(date(2026, 10, 5)) == date(2026, 10, 12)   # 週一 -> 下週一,不從一半開始
assert clusters.week_start_after(date(2026, 10, 11)) == date(2026, 10, 12)  # 週日 -> 明天
s = clusters.slots(1, date(2026, 10, 12))
assert [x["date"] for x in s] == [f"2026-10-{d}" for d in range(12, 19)]
assert [x["weekday"] for x in s if x["experiment"]] == ["Mon", "Thu"]
assert sum(x["experiment"] for x in s) == 2 and len(s) == 7
print("2. 一個 cluster = 週一到週日 7 天,中途不開新的;experiment 落在輪到的那兩天 ✅")

# --- 跨月的 cluster 不拆開 ---
c1 = clusters.create("P", "bluesky", date(2026, 9, 28), {"id": "hyp-1", "test": "t", "watch": "likes"}, None)
assert c1["cluster_id"] == 1 and clusters.month_of(c1) == "2026-09"   # 9/28–10/4,整個算 9 月
c2 = clusters.create("P", "bluesky", date(2026, 10, 5), None, learned_from=1)
assert c2["cluster_id"] == 2 and c2["learned_from"] == 1 and c2["experiment_weekdays"] == ["Tue", "Fri"]
for bad in (date(2026, 10, 7), date(2026, 10, 5)):
    try:
        clusters.create("P", "bluesky", bad, None, None); raise AssertionError(bad)
    except ValueError:
        pass
assert clusters.create("Q", "bluesky", date(2026, 10, 5), None, None)["cluster_id"] == 1
print("3. 跨月的 cluster 算在週一那個月;不是週一、或那週已有 cluster 都拒絕;編號依產品 × 平台各自從 1 開始 ✅")

# --- natural_id:核准時才給,退件不佔號 ---
a, b, c = posts.new_post_id(), posts.new_post_id(), posts.new_post_id()
assert len(a) == 36 and a.count("-") == 4
for pid in (a, b, c):
    posts.register(pid, "P", "bluesky", cluster_id=1, kind="cluster", experiment=False)
posts.update(b, status="rejected")
assert posts.approve(c)["natural_id"] == 1
assert posts.approve(a)["natural_id"] == 2
assert posts.approve(a)["natural_id"] == 2            # 核准兩次不換號
assert posts.get(b)["natural_id"] is None
x = posts.new_post_id()
posts.register(x, "P", "bluesky", cluster_id=1, kind="extra", experiment=None)
assert posts.approve(x)["natural_id"] == 3
try:
    posts.register(posts.new_post_id(), "P", "bluesky", cluster_id=1, kind="extra", experiment=True)
    raise AssertionError("extra 不該是 experiment")
except ValueError:
    pass
assert [r["kind"] for r in posts.in_cluster("P", "bluesky", 1)].count("extra") == 1
assert len(posts.in_cluster("P", "bluesky", 1, kind="cluster")) == 3
print("4. post_id 是完整 UUID;natural_id 在核准時給、退件不佔號、核准兩次不換號;extra 掛在 cluster 下但不算 experiment ✅")

# --- 舊貼文編成 cluster 0(照真實 tracking 檔的樣子造)---
T = tracking_review.TRACKING_PATH
rows = [
    {"post_id": "draft-a", "product": "P2", "channel": "bluesky", "post_ref": "at://1", "ts": "2026-09-15T19:00:00+00:00", "deleted_at": None},
    {"post_id": "draft-x", "product": "P2", "channel": "bluesky", "post_ref": None, "ts": "2026-09-16T00:00:00+00:00", "deleted_at": None},
    {"post_id": "draft-b", "product": "P2", "channel": "bluesky", "post_ref": "at://2", "ts": "2026-09-17T00:00:00+00:00", "deleted_at": None},
    # 之後的 checkpoint 更新會改 ts —— 順序要看第一次出現的時間
    {"post_id": "draft-a", "product": "P2", "channel": "bluesky", "post_ref": "at://1", "ts": "2026-09-30T00:00:00+00:00", "deleted_at": None},
    {"post_id": "draft-b", "product": "P2", "channel": "bluesky", "post_ref": "at://2", "ts": "2026-09-30T01:00:00+00:00", "deleted_at": "2026-09-30T01:00:00+00:00"},
]
T.write_text("".join(json.dumps(r) + "\n" for r in rows))
assert posts.backfill_from_tracking() == 2
pa, pb = posts.get("draft-a"), posts.get("draft-b")
assert (pa["natural_id"], pb["natural_id"]) == (1, 2)
assert pa["cluster_id"] == 0 and pa["experiment"] is None and pa["published_at"].startswith("2026-09-15")
assert pb["status"] == "deleted" and posts.get("draft-x") is None
assert posts.backfill_from_tracking() == 0
assert clusters.get("P2", "bluesky", 0)["status"] == "legacy"
print("5. 舊貼文:照第一次發佈的順序編號、進 cluster 0;沒發出去的不編;被刪的保留號碼並標 deleted;重跑不重複 ✅")

# --- 同一秒寫兩列,最新那列要贏(Python 和 SQL 都一樣)---
q = posts.new_post_id()
posts.register(q, "P3", "bluesky", cluster_id=1, kind="cluster", experiment=True)
posts.approve(q)
assert posts.get(q)["natural_id"] == 1
import duckdb
wh = R / "warehouse"; wh.mkdir()
sql = (Path(__file__).resolve().parent.parent / "warehouse/models/staging/stg_posts.sql").read_text()
os.chdir(wh)
got = duckdb.sql(sql + f" and post_id = '{q}'").fetchall()
assert got and got[0][0] == 1 and got[0][7] == "approved", got
csql = (Path(__file__).resolve().parent.parent / "warehouse/models/staging/stg_clusters.sql").read_text()
months = dict(duckdb.sql(f"select cluster_id, month from ({csql}) where product = 'P'").fetchall())
assert months[1] == "2026-09", months
print("6. 同一秒先登記再核准:Python 和 DuckDB 都讀到核准後那列;資料表的 month 用週一 ✅")

# --- 產品名:沒給 --product-name 時沿用人為這份文件取的名字(2026-10-07)---
os.chdir(R)
from contentmaster import product_registry as reg, pipeline
doc = R / "cat.rtfd"; doc.mkdir(); (doc / "TXT.rtf").write_text("{\\rtf1 cats}")
pipeline.topic_index.file_hash = lambda p: "HASH1"
assert pipeline._resolve_product_name(None, str(doc)) == "cat"                 # 從沒取過名 -> 檔名
assert pipeline._resolve_product_name("Fluffy roommate", str(doc)) == "Fluffy roommate"
assert pipeline._resolve_product_name(None, str(doc)) == "Fluffy roommate"      # 沿用人取的名字
assert pipeline._resolve_product_name("Other", str(doc)) == "Other"             # 人新打的名字永遠優先
assert pipeline._resolve_product_name(None, str(doc)) == "Other"
assert reg.lookup("cat")["explicit"] is False and reg.lookup("Other")["explicit"] is True
pipeline.topic_index.file_hash = lambda p: "HASH2"                              # 換了內容,同一個路徑
assert pipeline._resolve_product_name(None, str(doc)) == "Other"
# 舊紀錄沒有 explicit 欄位:名字跟檔名不同 = 人打的
assert reg._is_explicit({"name": "Fluffy roommate", "whitepaper": "cat.rtf"})
assert not reg._is_explicit({"name": "cat", "whitepaper": "/x/cat.rtfd"})
print("7. 產品名:人打的優先;沒打就沿用人上次給這份文件的名字;從沒取過才用檔名;LLM 不碰 ✅")
print("\nall passed")
