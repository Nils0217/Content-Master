import json, os, shutil, sys
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)

from contentmaster import audit, cli, discuss, error_index, image_generator, image_review, pipeline
from contentmaster import platform_rules, product_assets, product_registry, draft_queue
from contentmaster.draft_generator import DraftGenerator

P = "Fluffy roommate"

# === 圖片來源 ===
calls = []
image_generator.generate_image = lambda prompt, seed=None: calls.append(prompt) or b"\xff\xd8\xffGEN"
image_generator.build_image_prompt = lambda *a, **k: "a cat"
image_generator.image_format = lambda b: "jpg"
image_review.review = lambda *a, **k: None
pipeline.launch_streamlit = lambda: None

def run(name):
    draft = {"id": name, "text": "A post", "source": "topics"}
    pipeline._run_streamlit(P, "bluesky", [draft])
    return draft_queue._latest_entries()[name], draft

assert product_assets.image_source(P) == "auto"
e, _ = run("auto-empty")
assert len(calls) == 1 and e["image_path"], "自動、沒有檔案:要生圖"

product_assets.set_image_source(P, "supplied")
calls.clear()
e, d = run("only-mine-empty")
assert calls == [] and e["image_path"] is None and "no image" in d["characteristics"]
assert pipeline.step4b_generate_image({"id": "t"}, "A post", P) == (None, "") and calls == []
print("1. 只用自己的檔案、資料夾是空的:不呼叫生圖模型,貼文記成沒有圖(兩個介面都是) ✅")

folder = product_assets.product_dir(P); folder.mkdir(parents=True)
(folder / "mine.jpg").write_bytes(b"\xff\xd8\xffMINE")
e, _ = run("only-mine")
assert calls == [] and Path(e["image_path"]).read_bytes() == b"\xff\xd8\xffMINE"

product_assets.set_image_source(P, "generate")
e, _ = run("always-gen")
assert len(calls) == 1 and Path(e["image_path"]).read_bytes() == b"\xff\xd8\xffGEN"

product_assets.set_image_source(P, "auto")
calls.clear()
e, _ = run("auto-files")
assert calls == [] and Path(e["image_path"]).read_bytes() == b"\xff\xd8\xffMINE"
print("2. 三種設定都照選的做;有檔案也可以選擇生圖,預設行為沒有變 ✅")

product_assets.set_image_source(P, "supplied")
(folder / "mine.jpg").unlink(); (folder / "broken.jpg").mkdir()  # 不是檔案,不算
(folder / "bad.jpg").write_bytes(b"x"); (folder / "bad.jpg").chmod(0)
calls.clear()
e, _ = run("unreadable")
(folder / "bad.jpg").chmod(0o644)
assert calls == [] and e["image_path"] is None, "只用自己的檔案卻退回生圖"
assert pipeline.run_video("/nope.rtf", product_name=P) == 1 and calls == []
try:
    product_assets.set_image_source(P, "other"); raise AssertionError("亂寫的設定也收了")
except ValueError:
    pass
product_assets.set_image_source(P, "auto")
print("3. 檔案讀不到也不會偷偷生圖;影片在只用自己檔案時會明講不能做 ✅")

# === 錯誤索引 ===
for n in (322, 305):
    audit.log_event("publish", "failed", kind="too_long", error_type="PlatformContentRejected",
                    reason=f"Bluesky posts are capped at 300 characters, got {n}.", draft_id=f"d{n}")
audit.log_event("metrics", "unavailable", error_type="PlatformAPIError", reason="Network error reaching Bluesky")
audit.log_event("discuss", "model_error", model="m", error="ReadTimeout: slow")
audit.log_event("scheduler", "media_missing", post_id="p", path="/x/y.jpg")

rows = error_index.report()
assert rows[0]["count"] == 2 and "<n>" in rows[0]["signature"] and len(rows[0]["occurrences"]) == 2
assert any("model_error" in r["signature"] or "ReadTimeout" in r["signature"] for r in rows)
assert any("media_missing" in r["signature"] for r in rows)
assert error_index.report(min_count=2) == [rows[0]]
print("4. 同一種錯誤合併計次,保留實際發生的內容;模型沒回應和媒體遺失也算進去 ✅")

log = R / "ERROR_LOG.md"
log.write_text("# Errors\n\n### Capped characters when publishing\n\n**Kind:** `publish`\n**Cause:** capped characters "
               "were not checked before generation.\n**Fix:** enforce it.\n\n---\n\n"
               "### Cognee container\n\n**Cause:** container has no LLM.\n\n---\n\n"
               "### Capped budgets\n\n**Cause:** capped characters and posts ran late.\n\n---\n")
error_index.ERROR_LOG_PATH = log
rows = error_index.report()
assert [h for h, _ in rows[0]["known"]] == ["Capped characters when publishing"]
assert not any(h in ("Cognee container", "Capped budgets") for r in rows for h, _ in r["known"])
print("5. 找得到相符的過去紀錄,不相干的不會混進來 ✅")

sig = rows[0]["signature"]
seen = {}
def answering(model, prompt, timeout=90, **log):
    seen.update(prompt=prompt, model=model)
    return "Check the length before generation. The exact place is a guess."
discuss._complete = answering
p = error_index.propose_fix(sig)
assert p["status"] == "pending" and p["raw"].startswith("Check the length")
assert "got 305" in seen["prompt"] and "enforce it" in seen["prompt"]
assert error_index.report()[0]["proposal"]["status"] == "pending"
print("6. 提修法時模型看得到實際發生的內容和過去的紀錄;原樣保存回覆 ✅")

assert error_index.decide_proposal(sig, False, "too vague")["status"] == "rejected"
assert error_index.latest_proposal(sig)["note"] == "too vague"
assert error_index.decide_proposal(sig, True)["status"] == "accepted"
discuss._complete = lambda *a, **k: None
assert error_index.propose_fix(sig) is None
assert error_index.latest_proposal(sig)["status"] == "accepted", "模型沒回答卻蓋掉了舊提案"
try:
    error_index.decide_proposal("nothing", True); raise AssertionError
except KeyError:
    pass
print("7. 接受或拒絕由人決定;模型沒回答不會留下空提案,也不蓋掉舊的 ✅")

assert cli.main(["errors"]) == 0 and cli.main(["errors", "--propose", "99"]) == 1
assert cli.main(["errors", "--reject", "1", "--note", "no"]) == 0
assert error_index.latest_proposal(sig)["status"] == "rejected"
assert cli.main(["errors", "--propose", "1"]) == 1            # 模型沒回答要講,不能裝沒事
print("8. `contentmaster errors` 可以看、可以決定;模型沒回答會回報失敗 ✅")

# === 平台規則 ===
assert platform_rules.prompt_block("bluesky") == ""
gen = DraftGenerator()
prompts = []
import contentmaster.draft_generator as dg, requests
dg.requests.post = lambda url, json=None, timeout=None: (prompts.append(json["messages"][0]["content"]),
                                                          (_ for _ in ()).throw(requests.ConnectionError("off")))[1]
topics = [{"topic": "Trust", "brief": "Let the cat come to you."}]
def draft_prompts():
    prompts.clear()
    gen._llm_draft_posts_for_topics(P, "bluesky", topics, n=1)
    gen._llm_draft_posts(P, "bluesky", 1, "Cats are good.")
    return list(prompts)
from contentmaster.analysis import AnalysisResult
result = AnalysisResult(product=P, channel="bluesky", checkpoint="24h", n_prior_posts=0, historical_avg_score=None,
                        current_score=0.0, trend="no_history", prior_suggestion="",
                        prior_suggestion_effectiveness="unknown", evidence="None yet.")
before, advisor_before = draft_prompts(), discuss._proposal_prompt(P, "bluesky", result)
assert len(before) == 2 and all("VERIFIED ABOUT" not in x for x in before)

for args in (("", "a", "b"), ("bluesky", "", "b"), ("bluesky", "a", " ")):
    try:
        platform_rules.add(*args); raise AssertionError("缺欄位也收了")
    except ValueError:
        pass
rule = platform_rules.add("Bluesky", "Posts with links reach fewer people.", "Three products, 40 posts.")
platform_rules.add("mastodon", "Hashtags are how posts are found.", "Docs.")
after, advisor_after = draft_prompts(), discuss._proposal_prompt(P, "bluesky", result)
assert all("Posts with links reach fewer people." in x and "Hashtags" not in x for x in after + [advisor_after])
for b, a in zip(before + [advisor_before], after + [advisor_after]):
    assert a.replace(platform_rules.prompt_block("bluesky"), "") == b, "規則以外的指示被改到了"
print("9. 沒有規則時指示完全不變;有規則時寫草稿和顧問都看得到,其他平台的不會混進來 ✅")

platform_rules.retire(rule["id"])
assert platform_rules.prompt_block("bluesky") == ""
assert any(json.loads(l)["status"] == "active" for l in platform_rules.RULES_PATH.read_text().splitlines())
assert cli.main(["rules", "--platform", "bluesky", "--add", "x"]) == 1
assert cli.main(["rules", "--platform", "bluesky", "--add", "x", "--evidence", "y"]) == 0
assert cli.main(["rules", "--retire", "nope"]) == 1
print("10. 規則可以停用,紀錄保留;沒有證據不能新增 ✅")

src = Path(pipeline.__file__).parent
callers = [f.name for f in src.rglob("*.py") if "platform_rules.add(" in f.read_text()]
assert callers == ["cli.py"], callers
assert "platform_rules.add(" in (src.parent.parent / "streamlit_app.py").read_text()
print("11. 只有人操作的指令列和審核頁能新增規則,模型流程碰不到 ✅")
print("\nall passed")
