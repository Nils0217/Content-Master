import os, shutil, sys, json, re
from datetime import date
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)

from contentmaster import pipeline, posts, clusters, draft_queue, reasoning, modiqo_play, tracking_review
import contentmaster.discuss as d
import contentmaster.draft_generator as dg

P, CH = "Fluffy roommate", "bluesky"
TOPICS = [{"topic": f"Topic {i}", "brief": f"Brief for topic {i}, long enough to ground a post."}
          for i in range(1, 10)]
pipeline.step1_cognee_extract_topics = lambda path, product_name: TOPICS
pipeline.topic_index.load_index = lambda p: {"topics": TOPICS, "category": "A domestic cat"}
pipeline.launch_streamlit = lambda: None
pipeline.step5_publish = lambda *a, **k: (_ for _ in ()).throw(AssertionError("cluster 核准不該發佈"))
pipeline._resolve_product_name = lambda given, wp: P
# 角色檢查:測試裡不碰真實模型。預設全部「站對邊」,個別測試再改
from contentmaster import arm_check
arm_side = {"answer": None}
def fake_check(text, test):
    ans = arm_side["answer"](text) if arm_side["answer"] else None
    return {"makes_change": ans, "raw": "", "model": "fake"}
arm_check.makes_change = fake_check

# 顧問和裁判:用 2026-10-04 真實模型的原樣回覆
A = ("TEST: asking a question in the introduction instead of making a statement\n"
     "WHY: This format might suit the audience right now, as it encourages engagement.\n"
     "WATCH: 20% increase in likes")
B = ("TEST: Incorporate a reference to the trending \"cat in the hat\" in the post title.\n"
     "WHY: The current trend could attract more attention.\nWATCH: Reposts (shares)")
d._complete = lambda m, p, timeout=90: {d._MODEL_A: A, d._MODEL_B: B, d._JUDGE_MODEL: "PICK: A\nREASON: one clear change"}[m]

# 寫草稿的模型:照 prompt 要幾篇就給幾篇,TOPIC 用 prompt 裡第一個主題
prompts, short = [], {"n": 0}
class Resp:
    def __init__(self, c): self.c = c
    def raise_for_status(self): pass
    def json(self): return {"choices": [{"message": {"content": self.c}}]}
def post(url, json=None, timeout=None):
    pr = json["messages"][0]["content"]; prompts.append(pr)
    n = int(re.search(r"You are writing (\d+) short post", pr).group(1))
    if short["n"]:
        n, short["n"] = max(0, n - short["n"]), 0
    topic = re.search(r"^- (.+?) \(used", pr, re.M).group(1)
    return Resp("\n---\n".join(
        f"POST: Cats choose where they feel safe, number {i}.\nTOPIC: {topic}\nWHY: w\nIS: statement\n"
        "TESTING: opening = statement\nEVIDENCE-USED: none\nEVIDENCE-AGAINST: none" for i in range(n)))
dg.requests.post = post

WED = date(2026, 10, 7)
assert pipeline.run_cluster("doc.rtfd", CH, no_image=True, today=WED) == 0
c = clusters.get(P, CH, 1)
assert c["week_start"] == "2026-10-12" and c["status"] == "drafting" and c["learned_from"] is None
assert c["hypothesis"]["test"].startswith("asking a question") and c["experiment_weekdays"] == ["Mon", "Thu"]
start = [r for r in map(json.loads, open(reasoning.REASONING_PATH)) if r["checkpoint"] == "cluster-start"]
assert start and start[0]["hypothesis"]["id"] == c["hypothesis_id"]
print("1. 第一個 cluster:還沒有任何分析時,先由顧問和裁判決定 hypothesis(每一步都有存),再開下週一的 cluster ✅")

rows = posts.in_cluster(P, CH, 1)
assert [r["scheduled_for"] for r in rows] == [f"2026-10-{d}" for d in range(12, 19)]
assert [r["scheduled_for"] for r in rows if r["experiment"]] == ["2026-10-12", "2026-10-15"]
assert all(len(r["post_id"]) == 36 and r["kind"] == "cluster" and r["natural_id"] is None
           and r["status"] == "draft" for r in rows)
queued = {e["draft_id"]: e for e in draft_queue.find_pending()}
assert set(queued) == {r["post_id"] for r in rows}
assert all(queued[r["post_id"]]["cluster_id"] == 1 and queued[r["post_id"]]["experiment"] == r["experiment"]
           for r in rows)
print("2. 7 篇排在週一到週日;experiment 剛好在週一、週四;都是完整 UUID;核准前沒有編號 ✅")

main_p = [p for p in prompts if "Every post here makes exactly that change" in p]
exp_p = [p for p in prompts if "Do NOT make that change" in p]
assert len(main_p) == 1 and len(exp_p) == 1 and "You are writing 5" in main_p[0] and "You are writing 2" in exp_p[0]
assert not any("REQUIRED:" in p for p in prompts) and not any("HYPOTHESIS: `yes`" in p for p in prompts)
hid = c["hypothesis_id"]
assert all(queued[r["post_id"]].get("tests_hypothesis_id") == (None if r["experiment"] else hid) for r in rows)
print("3. 主要方向 5 篇被要求做那個改變、experiment 2 篇被要求不要做;不再另外要求對照組 ✅")

mon, tue = rows[0]["post_id"], rows[1]["post_id"]
row = pipeline.approve_for_schedule(tue, "Cats choose where they feel safe, number 0.")
assert row["natural_id"] == 1 and row["status"] == "approved"
assert draft_queue._latest_entries()[tue]["status"] == "approved"
assert not tracking_review.TRACKING_PATH.exists()
try:
    pipeline.approve_for_schedule(mon, "x" * 400); raise AssertionError("超長也被核准了")
except pipeline.NotSchedulable as e:
    assert any("300" in r or "long" in r.lower() for r in e.reasons), e.reasons
assert posts.get(mon)["natural_id"] is None and posts.get(mon)["status"] == "draft"
print("4. 核准 cluster 貼文:拿到編號 #1、狀態 approved、沒有發佈;會被平台拒絕的內容在核准時就擋下 ✅")

pipeline.record_rejected(mon)
draft_queue.update_entry(mon, status="rejected")
prompts.clear()
assert pipeline.run_cluster("doc.rtfd", CH, no_image=True, today=WED) == 0
assert len(clusters.for_product(P, CH)) == 1
mon_now = [r for r in posts.in_cluster(P, CH, 1) if r["scheduled_for"] == "2026-10-12"]
assert len(mon_now) == 2 and sum(r["status"] == "draft" for r in mon_now) == 1
assert len(prompts) == 1 and "Do NOT make that change" in prompts[0] and "You are writing 1" in prompts[0]
print("5. 退件後再跑一次:只補空出來的週一,而且照它的角色(experiment)寫,不重開 cluster ✅")

short["n"] = 2
prompts.clear()
assert pipeline.run_cluster("doc.rtfd", CH, no_image=True, today=date(2026, 10, 14)) == 0
c2 = clusters.get(P, CH, 2)
assert c2["week_start"] == "2026-10-19" and c2["experiment_weekdays"] == ["Tue", "Fri"]
assert len(posts.in_cluster(P, CH, 2)) == 7, "模型少給 2 篇時應該重試補齊"
print("6. 第二個 cluster:experiment 輪到週二、週五;模型少給時自動重試一次補齊 ✅")

for r in posts.in_cluster(P, CH, 2):
    posts.update(r["post_id"], status="published")
assert pipeline.run_cluster("doc.rtfd", CH, no_image=True, today=date(2026, 10, 14)) == 0
assert len(posts.in_cluster(P, CH, 2)) == 7
print("7. 七天都有貼文時再跑:什麼都不多做 ✅")

# 下一個 cluster 的 hypothesis 來自最新已決定的分析,並記下是從哪個 cluster 學來的
H2 = {"id": "hyp-from-analysis", "test": "end with a question", "watch": "replies"}
# 按 disagree 的假設不能被拿去用
modiqo_play.capture_failure(P, CH, "t", "x", analysis={"human_confirmed": False}, checkpoint="7d",
    metrics={"post_id": tue, "like_count": 0},
    trace={"post_id": tue, "next_hypothesis": {"id": "hyp-disagreed", "test": "x", "watch": "likes"}})
assert modiqo_play.find_best_prior(P, CH)["hypothesis"] is None
modiqo_play.capture_failure(P, CH, "t", "x", analysis={"human_confirmed": True}, checkpoint="7d",
    metrics={"post_id": tue, "like_count": 0}, trace={"post_id": tue, "next_hypothesis": H2})
modiqo_play.capture_failure(P, CH, "t", "x", analysis={"human_confirmed": False}, checkpoint="7d",
    metrics={"post_id": tue, "like_count": 0},
    trace={"post_id": tue, "next_hypothesis": {"id": "hyp-disagreed-2", "test": "y", "watch": "likes"}})
assert modiqo_play.find_best_prior(P, CH)["hypothesis"]["id"] == "hyp-from-analysis"
print("7b. 按 disagree 的 hypothesis 不會被下一個 cluster 用;用的是最近一個按 confirm 的 ✅")
assert pipeline.run_cluster("doc.rtfd", CH, no_image=True, today=date(2026, 10, 21)) == 0
c3 = clusters.get(P, CH, 3)
assert c3["hypothesis_id"] == "hyp-from-analysis" and c3["learned_from"] == 1
print("8. 有已決定的分析時,下一個 cluster 直接用它的 hypothesis,並記 learned_from = 1 ✅")

# 額外貼文:發佈時才進表,掛在當週的 cluster 下,不算 experiment
import datetime as _dt
class FakeDate(_dt.date):
    @classmethod
    def today(cls): return _dt.date(2026, 10, 14)   # cluster 1 那週是 10/12–10/18
import datetime
real_date = datetime.date
datetime.date = FakeDate
try:
    pipeline.record_published(P, CH, {"id": "extra-1", "post_ref": "at://x"})
finally:
    datetime.date = real_date
e1 = posts.get("extra-1")
assert e1["kind"] == "extra" and e1["cluster_id"] == 1 and e1["experiment"] is None
assert e1["status"] == "published" and e1["natural_id"] == 2
print("9. 額外貼文:發佈時登記為 extra,掛在當週 cluster 1 下、拿下一個編號,不進比較 ✅")

app = (Path(__file__).resolve().parent.parent / "streamlit_app.py").read_text()
assert "approve_for_schedule(" in app and "record_rejected(draft_id)" in app and "is_scheduled_kind(entry)" in app
term = Path(pipeline.__file__).read_text()
assert term.count("approve_for_schedule(draft[\"id\"]") == 1 and "record_rejected(draft[\"id\"])" in term
print("10. 兩個審核介面都走同一個核准 / 退件函式 ✅")

# 2026-10-07 真實 cluster 實跑:模型把章節編號拿掉,主要方向 5 篇全被丟掉
from contentmaster.draft_generator import _match_topic
REAL_T = [{"topic": "3. Basic Operating Procedure"}, {"topic": "2. Required Inputs"},
          {"topic": "1. Introduction"}, {"topic": "Step 1: Establish Trust"}]
for said, want in (("Basic Operating Procedure", "3. Basic Operating Procedure"),
                   ("Required Inputs", "2. Required Inputs"), ("Introduction", "1. Introduction"),
                   ("Step 1: Establish Trust", "Step 1: Establish Trust")):
    assert (_match_topic(said, REAL_T) or {}).get("topic") == want, said
assert _match_topic("Personal anecdote", REAL_T) is None
assert (_match_topic("Step 2: Identify the Cat's Preferences",
                     [{"topic": "Step 2: Identify the Cat\u2019s Preferences"}]) or {}).get("topic")
print("11. 模型拿掉章節編號、把彎引號打成直引號都對得到;不存在的主題照樣丟掉 ✅")

# 2026-10-10 真實實跑:6 次有 2 次太長(302、747 字)被整篇丟掉,--posts 1 就什麼都沒有
gen = dg.DraftGenerator()
calls = []
gen.revise_post = lambda name, text, fb, brief="", channel="": calls.append(fb) or "Short enough now."
assert gen._fit_to_channel("P", "x" * 747, "bluesky") == "Short enough now."
assert "at most 280 characters" in calls[0]
gen.revise_post = lambda *a, **k: None
assert gen._fit_to_channel("P", "x" * 302, "bluesky") is None
assert gen._fit_to_channel("P", "fits fine", "bluesky") == "fits fine"
print("12. 太長的草稿先請模型縮短一次;縮不下來才丟,而且會說;本來就合格的不動 ✅")

# --- 角色檢查(2026-10-24)---
# 真實 Gemma 回覆
assert arm_check.parse("MAKES_CHANGE: yes") is True and arm_check.parse("MAKES_CHANGE: no") is False
assert arm_check.parse("**MAKES_CHANGE:** Yes.") is True and arm_check.parse("No") is False
assert arm_check.parse("It depends on the reader.") is None
assert arm_check.verdict(True, {"makes_change": True}) == "wrong_side"
assert arm_check.verdict(False, {"makes_change": False}) == "wrong_side"
assert arm_check.verdict(True, {"makes_change": False}) == "ok"
assert arm_check.verdict(False, {"makes_change": None}) == "unchecked"
assert "FAILS" in arm_check.describe(True, {"makes_change": True})
print("13. 角色檢查:Gemma 的真實回覆讀得出來;experiment 做了改變、主要方向沒做改變 → 站錯邊 ✅")

# experiment 第一輪寫出有問句的(像真實週四那篇),要被丟掉、重寫
calls = {"n": 0}
def asks(text):
    calls["n"] += 1
    return calls["n"] <= 1          # 第一篇被判「有做改變」,之後的都沒有
arm_side["answer"] = asks
kept = pipeline._on_the_right_side([{"text": "how do you establish trust?"}, {"text": "Let it come to you."}],
                                   True, "include at least one question")
assert [k["text"] for k in kept] == ["Let it come to you."]
assert kept[0]["arm_check"]["verdict"] == "ok"
arm_side["answer"] = lambda t: None
kept = pipeline._on_the_right_side([{"text": "x"}], False, "t")
assert kept and kept[0]["arm_check"]["verdict"] == "unchecked"
print("14. 站錯邊的草稿在進審核頁前就丟掉;檢查不了的留著、標「沒檢查到」讓人判斷 ✅")

# 整個 cluster:experiment 第一次全站錯邊 → 重試補齊,審核頁看到的都站對邊
shutil.rmtree(R / "plays", ignore_errors=True)
first = {"done": False}
orig = pipeline._on_the_right_side
def flaky(drafts, is_exp, test):
    if is_exp and not first["done"]:
        first["done"] = True
        arm_side["answer"] = lambda t: True   # experiment 第一批:站錯邊
        out = orig(drafts, is_exp, test)
        arm_side["answer"] = lambda t: not is_exp
        return out
    arm_side["answer"] = lambda t: not is_exp
    return orig(drafts, is_exp, test)
pipeline._on_the_right_side = flaky
assert pipeline.run_cluster("doc.rtfd", CH, no_image=True, today=date(2026, 11, 4)) == 0
c = [c for c in clusters.for_product(P, CH) if c["week_start"] == "2026-11-09"][0]
rows = posts.in_cluster(P, CH, c["cluster_id"])
q = draft_queue._latest_entries()
assert len(rows) == 7 and all(q[r["post_id"]]["arm_check"]["verdict"] == "ok" for r in rows), \
    [q[r["post_id"]].get("arm_check") for r in rows]
pipeline._on_the_right_side = orig
print("15. 整週:experiment 第一批站錯邊被丟掉、自動重寫,7 篇進審核頁時全部站對邊 ✅")

# 2026-10-24 真實實跑:模型把指示裡的段落標題(GROUNDING)當主題名;重試中途印出 [error] 像是壞了
import io, contextlib
dg.requests.post = lambda url, json=None, timeout=None: Resp(
    "POST: Cats like routine.\nTOPIC: GROUNDING\nWHY: w\nIS: x\nTESTING: a = b\nEVIDENCE-USED: none\nEVIDENCE-AGAINST: none")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    out = dg.DraftGenerator().draft_posts({"name": P, "features": []}, CH, n=1, topics=TOPICS[:2],
                                          prior={"hypothesis": {"id": "h", "test": "t"}}, cluster_arm="main")
assert out == [] and "[attempt]" in buf.getvalue() and "[error]" not in buf.getvalue(), buf.getvalue()
dg.requests.post = post
prompts.clear()
dg.DraftGenerator()._llm_draft_posts_for_topics(P, CH, TOPICS[:2], {"hypothesis": {"id": "h", "test": "t"}},
                                                None, n=1, cluster_arm="main")
assert "never a heading of these instructions such as GROUNDING" in prompts[-1]
print("16. 主題只能從清單抄(不能寫 GROUNDING);cluster 重試中的一次失敗是提示,不是 [error] ✅")
print("\nall passed")
