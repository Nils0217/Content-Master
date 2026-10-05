import os, shutil, sys, json
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
assert h["watch"] == "likes" and h["watch_raw"] == "20% increase in likes", h
assert hyp.parse_hypothesis(REAL["llama_blank_lines"])["watch"] == "likes"
assert hyp.parse_hypothesis(REAL["mistral_shares"])["watch"] == "reposts"
assert hyp.parse_hypothesis(REAL["mistral_two"])["watch"] == "likes"
assert hyp.parse_hypothesis(REAL["llama_blank_lines"])["why"].startswith("This might suit")
print("1. 四種真實回覆都解析得出 TEST/WHY/WATCH;'shares' 認成 reposts,兩個數字取第一個 ✅")

for deco in ("**TEST:** x\n**WATCH:** likes", "1. TEST: x\n2. WATCH: likes", "- TEST: x\n- WATCH: likes",
             "> TEST: x\n> WATCH: likes", "-- TEST: x\n-- WATCH: likes", "Test: x\nWatch: likes"):
    got = hyp.parse_hypothesis(deco)
    assert got and got["test"] == "x" and got["watch"] == "likes", (deco, got)
print("2. 粗體、編號、bullet、引號、'-- '、小寫欄位名都不影響 ✅")

assert hyp.watch_metric("click-through rate and bounce rate") is None
assert hyp.watch_metric("engagement in Romania") is None
assert hyp.parse_hypothesis("TEST: x\nWATCH: ad clicks")["watch"] is None
assert "not a number this system reads" in hyp.render(hyp.parse_hypothesis("TEST: x\nWATCH: ad clicks"))
print("3. 系統讀不到的數字（點擊、跳出率、地區）不會被當成 like 等;原文保留並標明 ✅")

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
assert "TEST:" in cold and "WHY:" in cold and "WATCH:" in cold
assert "WRONG" not in cold and "wrong" not in cold.replace("no right or wrong", "")
assert "likes, reposts, replies or bookmarks" in cold
assert "Step 1: Establish Trust; Comparing lifestyle risks" in cold
assert 'The post being reviewed said: "Introducing Fluffy"' in cold
assert "You know nothing about who follows this account" in cold
warm = d._proposal_prompt("Fluffy roommate", "bluesky", mk(True))
assert "Trend:" in warm and "TEST:" not in warm
print("6. 沒基準線:問 TEST/WHY/WATCH,沒有「什麼代表錯」,只能選四個數字,有主題清單和貼文原文 ✅")


# --- synthesize_strategy:裁判只選,不改寫;每一步都存 ---
def fake(replies):
    calls = []
    def _c(model, prompt, timeout=90):
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

# --- 兩個審核介面共用的畫面 ---
fake({d._MODEL_A: REAL["llama_question"], d._MODEL_B: REAL["mistral_shares"],
      d._JUDGE_MODEL: "PICK: A\nREASON: r"})
s = d.synthesize_strategy("P", "bluesky", mk(False), post_id="draft-6", checkpoint="7d")
v = hyp.review_view(s.hypothesis, reasoning.load(s.reasoning_id))
assert v["headline"].startswith("Test: asking a question") and "Watch: likes" in v["headline"]
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
                            improvement_note=hyp.render(H), analysis={}, checkpoint="7d",
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
print("\nall passed")
