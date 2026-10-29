import os, shutil, sys, json, inspect
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)

from contentmaster import hypothesis as hyp, reasoning
import contentmaster.discuss as d
from contentmaster.analysis import AnalysisResult

# --- 真實模型的原樣回覆（2026-10-04,用新的 prompt 實跑出來的,不是手打的）---
REAL = {
    "llama_question": ("TEST: asking a question in the introduction instead of making a statement\n"
        "WHY: This format might suit the audience right now, as it encourages engagement and "
        "invites readers to participate in the account's content creation process.\n"
        "WATCH: 20% increase in likes"),
    "llama_blank_lines": ("TEST: Include a photo of a cat in the post, specifically a black cat, to "
        "tie in with the trending context.\n\nWHY: This might suit the audience right now because "
        "the \"cat in the hat\" trend is currently experiencing a breakout, making it a relevant "
        "and timely addition.\n\nWATCH: 100 likes"),
    "mistral_shares": ("TEST: Incorporate a reference to the trending \"cat in the hat\" in the post "
        "title or content.\nWHY: The current trend could attract more attention and engagement "
        "from the audience, particularly due to the breakout of \"cat in the hat\" searches.\n"
        "WATCH: Reposts (shares) as this trend might encourage users to share the post with "
        "others who are also interested in the topic."),
    "mistral_two": ("TEST: Incorporate the trending term \"cat in the hat\" by referencing the "
        "beloved Dr. Seuss character in the post title or content.\nWHY: The term \"cat in the "
        "hat\" is currently trending, and incorporating it may attract more attention and "
        "engagement from users interested in cats, potentially increasing likes, reposts, "
        "replies, or bookmarks.\nWATCH: Likes or reposts as an indicator of success, as they are "
        "highly correlated with engagement and reach."),
}
JUDGE_REAL = ("PICK: A\nREASON: It changes a single clear thing (introduction format) that stays "
              "within the account's roommate theme without introducing an unrelated trend.")

h = hyp.parse_hypothesis(REAL["llama_question"])
assert h["test"] == "asking a question in the introduction instead of making a statement", h
# 2026-10-22: 看什麼是固定的(黏著度 + 追蹤變化量);模型自己寫的 WATCH 只留原文
assert h["watch"] == hyp.WATCH_FIXED and h["watch_raw"] == "20% increase in likes", h
for k in REAL:
    assert hyp.parse_hypothesis(REAL[k])["watch"] == hyp.WATCH_FIXED
assert hyp.parse_hypothesis(REAL["llama_blank_lines"])["why"].startswith("This might suit")
print("1. 四種真實回覆都解析得出 TEST/WHY;要看的數字固定,模型寫的 WATCH 只保留原文 ✅")

for deco in ("**TEST:** x\n**WATCH:** likes", "1. TEST: x\n2. WATCH: likes", "- TEST: x\n- WATCH: likes",
             "> TEST: x\n> WATCH: likes", "-- TEST: x\n-- WATCH: likes", "Test: x\nWatch: likes"):
    got = hyp.parse_hypothesis(deco)
    assert got and got["test"] == "x" and got["watch_raw"] == "likes", (deco, got)
print("2. 粗體、編號、bullet、引號、'-- '、小寫欄位名都不影響 ✅")

assert hyp.watch_metric("click-through rate and bounce rate") is None
assert hyp.watch_metric("engagement in Romania") is None
assert hyp.parse_hypothesis("TEST: x\nWATCH: ad clicks")["watch"] == hyp.WATCH_FIXED
assert hyp.parse_hypothesis("TEST: x")["watch"] == hyp.WATCH_FIXED      # 不寫 WATCH 也成立
print("3. 模型寫什麼數字都不影響:一律看黏著度 + 追蹤變化量 ✅")

assert hyp.parse_hypothesis("Try a black cat adoption story. It could work because...") is None
assert hyp.parse_hypothesis("") is None and hyp.parse_hypothesis(None) is None
print("4. 沒有 TEST 的散文 -> None,不硬湊 ✅")

assert hyp.parse_pick(JUDGE_REAL) == ("A", JUDGE_REAL.split("REASON: ")[1])
assert hyp.parse_pick("PICK: Advisor B\nREASON: r")[0] == "B"
assert hyp.parse_pick("**PICK:** b\nREASON: r")[0] == "B"
assert hyp.parse_pick("Both are good.")[0] is None
print("5. 裁判回覆:真實格式、'Advisor B'、粗體小寫都認得;沒選就是 None ✅")


def mk(has_baseline):
    return AnalysisResult(product="Fluffy roommate", channel="bluesky", checkpoint="7d",
        n_prior_posts=0, historical_avg_score=None, current_score=0.0, trend="no_history",
        prior_suggestion="", prior_suggestion_effectiveness="unknown", evidence="E",
        trending_context="Trending context: cat in the hat", has_baseline=has_baseline)

cold = d._proposal_prompt("Fluffy roommate", "bluesky", mk(False), post_text="Introducing Fluffy",
                          topic_names=["Step 1: Establish Trust", "Comparing lifestyle risks"])
assert "TEST:" in cold and "WHY:" in cold and "WATCH:" not in cold
assert "WRONG" not in cold and "wrong" not in cold.replace("no right or wrong", "")
assert "follower stickiness" in cold and "follower change" in cold and "you do not choose" in cold
assert "Step 1: Establish Trust; Comparing lifestyle risks" in cold
assert 'The post being reviewed said: "Introducing Fluffy"' in cold
assert "You know nothing about who follows this account" in cold
warm = d._proposal_prompt("Fluffy roommate", "bluesky", mk(True))
assert "Trend:" in warm and "TEST:" not in warm
img = d._proposal_prompt("Fluffy roommate", "bluesky", mk(False), post_text="x",
                         image_note="The post's image, as described by an image model: a ginger cat.")
assert "a ginger cat" in img and "TEXT only" in img and "never add, remove or change an image" in img
print("6. 沒基準線:問 TEST/WHY,固定看黏著度和追蹤變化;有主題清單、貼文原文、Gemma 的圖片描述,只准改文字 ✅")


# --- synthesize_strategy:裁判只選,不改寫;每一步都存 ---
_REAL_COMPLETE = d._complete

def fake(replies):
    calls = []
    def _c(model, prompt, timeout=90, **log):
        calls.append((model, prompt))
        return replies.get(model)
    d._complete = _c
    return calls

events = []
d.audit.log_event = lambda stage, event, **kw: events.append((stage, event, kw))

fake({d._MODEL_A: REAL["llama_question"], d._MODEL_B: REAL["mistral_shares"],
      d._JUDGE_MODEL: "<think>hmm</think>PICK: B\nREASON: one clear change."})
s = d.synthesize_strategy("Fluffy roommate", "bluesky", mk(False), post_id="draft-1",
                          checkpoint="7d", post_text="Introducing Fluffy")
want = hyp.parse_hypothesis(REAL["mistral_shares"])
assert s.hypothesis["test"] == want["test"], s.hypothesis   # B 的原文,一字不改
assert s.hypothesis["picked"] == "B" and s.hypothesis["pick_reason"] == "one clear change."
assert s.hypothesis["model"] == d._MODEL_B and s.hypothesis["id"].startswith("hyp-")
assert s.text == hyp.render(s.hypothesis) and "Final strategy" not in s.text
print("7. 裁判只選 B,存下的是 B 的原文,不是裁判的改寫 ✅")

row = reasoning.load(s.reasoning_id)
assert row["post_id"] == "draft-1" and row["checkpoint"] == "7d" and row["outcome"] == "picked"
assert [x["step"] for x in row["steps"]] == ["advisor", "advisor", "judge"]
assert all(x["prompt"] for x in row["steps"]) and row["steps"][0]["raw"] == REAL["llama_question"]
assert row["steps"][2]["raw"].startswith("<think>")       # 原樣保留,包含思考區塊
assert row["hypothesis"]["id"] == s.hypothesis["id"]
disc = [kw for st, ev, kw in events if st == "discuss"]
assert disc and all(kw.get("post_id") == "draft-1" and kw.get("checkpoint") == "7d" for kw in disc)
print("8. 三步（兩位顧問 + 裁判）的 prompt 和原始回覆都存了,而且標著 post_id/checkpoint ✅")

fake({d._MODEL_A: REAL["llama_question"], d._MODEL_B: REAL["mistral_shares"],
      d._JUDGE_MODEL: "Both have merit; combine them."})
s = d.synthesize_strategy("P", "bluesky", mk(False), post_id="draft-2", checkpoint="7d")
assert s.hypothesis["picked"] == "A" and "no readable pick" in s.hypothesis["pick_reason"]
assert reasoning.load(s.reasoning_id)["outcome"] == "judge_unusable"
print("9. 裁判沒給可讀的選擇 -> 用 A,並明確記成 judge_unusable ✅")

# 2026-10-29 實際發生:qwen3 跑了約 180 秒後沒回(raw None),之前也記成 judge_unusable
fake({d._MODEL_A: REAL["llama_question"], d._MODEL_B: REAL["mistral_shares"]})
s = d.synthesize_strategy("P", "bluesky", mk(False), post_id="draft-2b", checkpoint="24h")
assert s.hypothesis["picked"] == "A" and "did not respond" in s.hypothesis["pick_reason"]
assert reasoning.load(s.reasoning_id)["outcome"] == "judge_no_response"
v = hyp.review_view(s.hypothesis, reasoning.load(s.reasoning_id))
assert "did not respond" in v["picked"]          # 兩個審核介面都從 review_view 讀
print("9b. 裁判根本沒回 -> judge_no_response,和讀不出來分開 ✅")

_post = d.requests.post
def _timeout(*a, **kw): raise d.requests.ReadTimeout("read timed out (read timeout=180)")
d.requests.post = _timeout
events.clear()
assert _REAL_COMPLETE(d._JUDGE_MODEL, "p", timeout=180, post_id="draft-2b") is None
d.requests.post = _post
err = [kw for st, ev, kw in events if (st, ev) == ("discuss", "model_error")]
assert err and err[0]["error"].startswith("ReadTimeout") and err[0]["post_id"] == "draft-2b"
assert err[0]["model"] == d._JUDGE_MODEL and err[0]["timeout"] == 180
print("9c. 模型呼叫失敗時,原因(逾時/連不上…)記進 audit 的 discuss/model_error ✅")

fake({d._MODEL_A: "Try black cats. It might work.", d._MODEL_B: "Use cat in the hat."})
s = d.synthesize_strategy("P", "bluesky", mk(False), post_id="draft-3", checkpoint="7d")
assert s.hypothesis is None and "Try black cats" in s.text and "cat in the hat" in s.text
assert reasoning.load(s.reasoning_id)["outcome"] == "none_usable"
fake({})
s = d.synthesize_strategy("P", "bluesky", mk(False), post_id="draft-4", checkpoint="7d")
assert s.hypothesis is None and reasoning.load(s.reasoning_id)["outcome"] == "no_response"
print("10. 回了但讀不出 vs. 根本沒回:兩種空結果分得開,原文都留著 ✅")

fake({d._MODEL_A: "no test here", d._MODEL_B: REAL["mistral_two"]})
s = d.synthesize_strategy("P", "bluesky", mk(False), post_id="draft-5", checkpoint="7d")
assert s.hypothesis["picked"] == "B" and reasoning.load(s.reasoning_id)["outcome"] == "only_one_usable"
assert len(reasoning.load(s.reasoning_id)["steps"]) == 2        # 只有一個可用,不叫裁判
print("11. 只有一位顧問可用 -> 直接用它,不浪費一次裁判 ✅")

# 2026-10-22: 看的數字固定之後,沒寫 WATCH 的提案也能參選
fake({d._MODEL_A: "TEST: open with a question\nWHY: invites replies",
      d._MODEL_B: REAL["mistral_two"], d._JUDGE_MODEL: "PICK: A\nREASON: r"})
s = d.synthesize_strategy("P", "bluesky", mk(False), post_id="draft-7", checkpoint="7d")
assert s.hypothesis["picked"] == "A" and reasoning.load(s.reasoning_id)["outcome"] == "picked"
print("11b. 只寫 TEST/WHY 的提案照樣能參選(看什麼由系統固定)✅")

# --- 兩個審核介面共用的畫面 ---
fake({d._MODEL_A: REAL["llama_question"], d._MODEL_B: REAL["mistral_shares"],
      d._JUDGE_MODEL: "PICK: A\nREASON: r"})
s = d.synthesize_strategy("P", "bluesky", mk(False), post_id="draft-6", checkpoint="7d")
v = hyp.review_view(s.hypothesis, reasoning.load(s.reasoning_id))
assert v["headline"].startswith("Test: asking a question") and f"Watch: {hyp.WATCH_FIXED}" in v["headline"]
assert v["picked"].startswith(f"Advisor A ({d._MODEL_A}): r")
assert v["other"].startswith("Advisor B proposed: Test: Incorporate")
assert len(v["steps"]) == 3 and v["steps"][2]["label"].startswith("Judge")
v = hyp.review_view(None, {"outcome": "none_usable", "steps": []})
assert "Neither advisor" in v["problem"] and not v["headline"]
print("12. 審核畫面:一行重點、誰被選和理由、沒被選的那個、三步細節;沒有假設時說原因 ✅")

# --- 寫草稿:hypothesis 送到草稿那一步,而且要有一篇宣告在測它 ---
import contentmaster.draft_generator as dg
H = {**hyp.parse_hypothesis(REAL["llama_question"]), "id": "hyp-test1"}
seen = {}
class Resp:
    def __init__(self, content): self.content = content
    def raise_for_status(self): pass
    def json(self): return {"choices": [{"message": {"content": self.content}}]}
def post(url, json=None, timeout=None):
    seen["prompt"] = json["messages"][0]["content"]
    return Resp("POST: Have you ever wondered why your cat picks one spot?\nTOPIC: Step 1: Establish Trust\n"
                "WHY: w\nIS: question\nTESTING: opening = question\nEVIDENCE-USED: none\n"
                "EVIDENCE-AGAINST: none\nHYPOTHESIS: yes\n---\n"
                "POST: Let the cat come to you first.\nTOPIC: Step 1: Establish Trust\nWHY: w\nIS: statement\n"
                "TESTING: opening = statement\nEVIDENCE-USED: none\nEVIDENCE-AGAINST: none\nHYPOTHESIS: no\n")
dg.requests.post = post
topics = [{"topic": "Step 1: Establish Trust", "brief": "Allow the cat to approach voluntarily."}]
prior = {"winning_text": "Introducing Fluffy", "last_metrics": {}, "hypothesis": H}
out = dg.DraftGenerator()._llm_draft_posts_for_topics("Fluffy roommate", "bluesky", topics, prior, None, n=2)
assert "HYPOTHESIS TO TEST" in seen["prompt"] and H["test"] in seen["prompt"]
assert "HYPOTHESIS: `yes`" in seen["prompt"]
assert [x["tests_hypothesis_id"] for x in out] == ["hyp-test1", None], out
print("13. 寫草稿的 prompt 帶著 hypothesis;宣告 yes 的那篇記上 hypothesis id ✅")

prior_no = {"winning_text": "x", "last_metrics": {}}
dg.DraftGenerator()._llm_draft_posts_for_topics("Fluffy roommate", "bluesky", topics, prior_no, None, n=2)
assert "HYPOTHESIS" not in seen["prompt"]
print("14. 沒有 hypothesis 時,prompt 不多出這一段 ✅")

# 2026-10-05: 沒基準線時不要求對照組 —— 那時沒有證據可以違背,hypothesis 才是測試
notes = []
import builtins
_print = builtins.print
builtins.print = lambda *a, **k: notes.append(" ".join(map(str, a)))
dg.DraftGenerator()._llm_draft_posts_for_topics("Fluffy roommate", "bluesky", topics, prior, None, n=1)
builtins.print = _print
assert "REQUIRED:" not in seen["prompt"] and "HYPOTHESIS TO TEST" in seen["prompt"], seen["prompt"]
assert not any("required to go against" in n for n in notes), notes
warm_prior = {"winning_text": "x", "last_metrics": {}, "has_baseline": True}
dg.DraftGenerator()._llm_draft_posts_for_topics("Fluffy roommate", "bluesky", topics, warm_prior, None, n=1)
assert "REQUIRED:" in seen["prompt"], "有基準線時對照組規則要照舊"
print("14b. 沒基準線:不要求對照組,只測 hypothesis（不會一篇同時改兩件事）;有基準線:對照組照舊 ✅")

from contentmaster import pipeline
assert "tests_hypothesis_id" in pipeline.LABEL_KEYS
app = (Path(__file__).resolve().parent.parent / "streamlit_app.py").read_text()
assert "LABEL_KEYS" in app and '"evidence_used", "evidence_against")' not in app
print("15. 兩個介面用同一份欄位清單;streamlit 不再自己寫一份 ✅")

# --- 發文當下的環境 ---
class Boom:
    def account_snapshot(self): raise RuntimeError("bluesky down")
class Ok:
    def account_snapshot(self): return {"followers": 0, "following": 1, "posts": 14}
pipeline.get_platform = lambda ch: Boom()
env = pipeline._environment({"has_image": True}, "bluesky")
assert env["followers"] is None and env["has_image"] is True and env["weekday"]
pipeline.get_platform = lambda ch: Ok()
env = pipeline._environment({"has_video": True}, "bluesky")
assert env["followers"] == 0 and env["posts"] == 14 and env["has_video"] is True
assert hyp.describe_environment(env).startswith("0 followers")
print("16. 環境:追蹤者 0 記成 0,查不到記成未知（不是 0）,查不到也不擋發文 ✅")

# --- history 追得回去,下一篇讀得到 hypothesis ---
from contentmaster import modiqo_play
entry = {"post_id": "draft-9", "tests_hypothesis_id": "hyp-old", "environment": env}
trace = pipeline._trace_for(entry, hypothesis=H, reasoning_id="rsn-9")
modiqo_play.capture_failure("Fluffy roommate", "bluesky", "t", "hypothesis at 7d",
                            improvement_note=hyp.render(H), analysis={"human_confirmed": True}, checkpoint="7d",
                            metrics={"post_id": "draft-9", "like_count": 0}, trace=trace)
row = json.loads(modiqo_play.HISTORY_PATH.read_text().splitlines()[-1])
assert row["post_id"] == "draft-9" and row["tests_hypothesis_id"] == "hyp-old"
assert row["next_hypothesis"]["id"] == "hyp-test1" and row["reasoning_id"] == "rsn-9"
assert row["environment"]["followers"] == 0
prior = modiqo_play.find_best_prior("Fluffy roommate", "bluesky")
assert prior["hypothesis"]["test"] == H["test"]
assert prior["has_baseline"] is False
print("17. history 一列就看得到:哪篇、測了哪個假設、當時環境、下一篇要測什麼、每一步在哪 ✅")
print("    下一篇寫草稿時讀得到那個假設 ✅")

# --- 測完要收尾:看黏著度(回頭互動者比例),附那週的追蹤變化(2026-10-22)---
from contentmaster import metrics_store, posts, clusters
from contentmaster import stickiness as stk
from datetime import date
Hl = {"id": "hyp-L", "test": "open with a question", "watch": hyp.WATCH_FIXED}
def published(pid, when, engagers):
    posts.register(pid, "Fluffy roommate", "bluesky", cluster_id=1, kind="cluster", experiment=False)
    posts.update(pid, status="published", published_at=when)
    if engagers is not None:
        metrics_store.write_metrics(pid, "bluesky", {"like_count": len(engagers),
            "engagers": {"likers": sorted(engagers), "reposters": [], "repliers": []}})
clusters.create("Fluffy roommate", "bluesky", date(2026, 10, 12), Hl, None)
published("old1", "2026-10-12T18:00:00+00:00", {"A", "B"})
published("old2", "2026-10-13T18:00:00+00:00", {"C"})
published("cmp1", "2026-10-14T18:00:00+00:00", {"A"})          # 1/1 回頭 → 100%
published("cmp2", "2026-10-15T18:00:00+00:00", {"E"})          # 0/1 → 0%
published("hit", "2026-10-16T18:00:00+00:00", {"A", "B"})      # 2/2 → 100%
published("miss", "2026-10-16T19:00:00+00:00", {"Y", "Z"})     # 0/2 → 0%
published("nobody", "2026-10-17T18:00:00+00:00", set())
published("noread", "2026-10-17T19:00:00+00:00", None)
assert stk.stickiness("hit", "Fluffy roommate", "bluesky") == {"value": 1.0, "engagers": 2, "returning": 2,
                                                               "known": True, "unknown_prior": 0}
for snap in ((0, "2026-10-12T17:00:00+00:00"), (2, "2026-10-15T00:00:00+00:00"), (3, "2026-10-19T09:00:00+00:00")):
    with stk.ACCOUNT_PATH.open("a") as fh:
        fh.write(json.dumps({"ts": snap[1], "channel": "bluesky", "followers": snap[0]}) + "\n")
r = hyp.evaluate(Hl, "hit", "Fluffy roommate", "bluesky", ["cmp1", "cmp2"], env, "7d")
assert r["outcome"] == "moved" and r["stickiness"] == 1.0 and r["comparison_median"] == 0.5, r
assert r["follower_change"]["change"] == 3 and r["follower_change"]["from"] == 0
r0 = hyp.evaluate(Hl, "miss", "Fluffy roommate", "bluesky", ["cmp1", "cmp2"], env, "7d")
assert r0["outcome"] == "did_not_move" and r0["stickiness"] == 0.0
assert hyp.evaluate(Hl, "nobody", "Fluffy roommate", "bluesky", ["cmp1", "cmp2"], env, "7d")["outcome"] == "no_engagers"
assert hyp.evaluate(Hl, "noread", "Fluffy roommate", "bluesky", ["cmp1", "cmp2"], env, "7d")["outcome"] == "unknown"
assert hyp.evaluate(Hl, "hit", "Fluffy roommate", "bluesky", ["cmp1"], env, "7d")["outcome"] == "no_comparison"
assert hyp.evaluate(None, "hit", "Fluffy roommate", "bluesky", [], env, "7d") is None
print("18. 黏著度 = 互動者裡以前互動過的比例;沒人互動是「沒有黏著度」不是 0;讀不到誰互動是「未知」;追蹤變化看整週 ✅")

for x in (r, r0):
    line = hyp.result_line(x).lower()
    assert "wrong" not in line and "fail" not in line, line
    assert "0 followers" in line and "followers that week: +3 (0 → 3)" in line, line
assert "signal, not proof" in hyp.result_line(r)
print("19. 結果只說「動了 / 在這些條件下沒動」,附上發文環境;沒有對錯 ✅")

# history 讀比較對象:排除測同一個假設的貼文,也排除自己
modiqo_play.HISTORY_PATH.write_text("")
for pid, hid, likes in (("p1", None, 0), ("p2", "hyp-L", 9), ("p3", "hyp-other", 1), ("self", None, 7)):
    modiqo_play.capture_failure("Fluffy roommate", "bluesky", "t", "x", analysis={}, checkpoint="7d",
        metrics={"post_id": pid, "like_count": likes}, trace={"post_id": pid, "tests_hypothesis_id": hid})
got = sorted(m["like_count"] for m in modiqo_play.readings_without("bluesky", "7d", "hyp-L", {"self"}))
assert got == [0, 1], got
print("20. 比較對象排除「也在測這個假設的」和「這篇自己」✅")

# 從頭到尾:一篇測了假設的貼文,7d 分析時要讀出結果並告訴顧問
reasoning.record("rsn-L", post_id="p0", checkpoint="7d", product="Fluffy roommate", channel="bluesky",
                 mode="hypothesis", steps=[], outcome="picked", result_text="", hypothesis=Hl)
modiqo_play.capture_failure("Fluffy roommate", "bluesky", "t", "x", analysis={}, checkpoint="7d",
    metrics={"post_id": "cmp1", "like_count": 1}, trace={"post_id": "cmp1"})
modiqo_play.capture_failure("Fluffy roommate", "bluesky", "t", "x", analysis={}, checkpoint="7d",
    metrics={"post_id": "cmp2", "like_count": 1}, trace={"post_id": "cmp2"})
pipeline._pull_checkpoint_metrics = lambda entry, cp: {"post_id": "hit", "like_count": 2,
    "repost_count": 0, "reply_count": 0, "bookmark_count": 0, "engagement_score": 2.0}
prompts = fake({d._MODEL_A: REAL["llama_question"], d._MODEL_B: REAL["mistral_two"],
                d._JUDGE_MODEL: "PICK: A\nREASON: r"})
pipeline.synthesize_strategy = d.synthesize_strategy
tester = {"post_id": "hit", "product": "Fluffy roommate", "channel": "bluesky",
          "final_text": "Ever wonder why?", "tests_hypothesis_id": "hyp-L", "environment": env,
          "image_description": "a ginger cat on a porch"}
_, a_res, strat = pipeline._pull_and_analyze(tester, "7d")
assert a_res.hypothesis_result["outcome"] == "moved" and a_res.hypothesis_result["stickiness"] == 1.0
assert "RESULT OF THE TEST THIS POST WAS RUNNING" in prompts[0][1] and "it moved" in prompts[0][1]
assert "a ginger cat on a porch" in prompts[0][1] and "TEXT only" in prompts[0][1]
assert pipeline._trace_for(tester, strat, hypothesis_result=a_res.hypothesis_result)["hypothesis_result"]["outcome"] == "moved"
assert a_res.as_dict()["hypothesis_result"]["hypothesis_id"] == "hyp-L"
_, plain, _ = pipeline._pull_and_analyze({**tester, "tests_hypothesis_id": None}, "7d")
assert plain.hypothesis_result is None
print("21. 從頭到尾:測了假設的貼文,7d 分析讀出黏著度結果、寫進分析和 history,顧問看得到結果和圖片描述 ✅")

app = (Path(__file__).resolve().parent.parent / "streamlit_app.py").read_text()
import contentmaster.analysis as an
assert "hyp.result_line(analysis['hypothesis_result'])" in app
assert "result_line(result.hypothesis_result)" in inspect.getsource(an.confirm_with_human)
print("22. 兩個審核介面都用同一句 result_line 顯示結果 ✅")
print("\nall passed")
