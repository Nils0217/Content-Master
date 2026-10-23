import os, shutil, sys, json
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)

from contentmaster import image_review as ir, pipeline

# --- Gemma 的真實回覆(2026-10-22,gemma3:4b 看兩張已發佈的圖)---
GOOD = ("DESCRIPTION: The image depicts a woman sitting on a porch, gently petting a ginger cat.\n"
        "MATCHES: yes\nTEXT: no\nINVENTED: no")
DEVICE = ("DESCRIPTION: The image depicts a woman cuddling with a cat on a couch, illuminated by a "
          "modern light device.\nMATCHES: yes\nTEXT: no\nINVENTED: yes")

g = ir.parse(GOOD)
assert g == {"description": "The image depicts a woman sitting on a porch, gently petting a ginger cat.",
             "matches": True, "has_text": False, "invented": False, "parsed": True}, g
assert ir.problems({**g}) == []
dv = ir.parse(DEVICE)
assert dv["invented"] is True and dv["parsed"]
assert ir.problems(dv) == ["The image shows a device or product the post does not mention."]
print("1. Gemma 真實回覆:正常的圖沒有警告;捏造出裝置的那張(draft-98f2d720)被抓出來 ✅")

for deco in ("**DESCRIPTION:** a cat\n**MATCHES:** Yes.\n**TEXT:** No\n**INVENTED:** no",
             "1. DESCRIPTION: a cat\n2. MATCHES: yes\n3. TEXT: no\n4. INVENTED: no",
             "- Description: a cat\n- Matches: yes, it does\n- Text: no\n- Invented: no"):
    x = ir.parse(deco)
    assert x["parsed"] and x["description"] == "a cat" and x["matches"] is True, (deco, x)
print("2. 粗體、編號、bullet、小寫欄位、「yes, it does」都讀得出來 ✅")

bad = ir.parse("This is a nice picture of a cat.")
assert bad["parsed"] is False
assert ir.problems({**bad, "error": None})[0].startswith("The image model could not check")
assert ir.review(None, "x") is None
ir.requests.post = lambda *a, **k: (_ for _ in ()).throw(ir.requests.ConnectionError("ollama down"))
down = ir.review(b"img", "x")
assert down["parsed"] is False and "ConnectionError" in down["error"]
print("3. 讀不出 / 模型掛掉 → 標成「沒檢查到」,不會被當成通過;沒有圖就是 None ✅")

lab = ir.as_labels(dv)
assert lab["image_description"].startswith("The image depicts") and lab["image_check"]["invented"] is True
assert "image_description" in pipeline.LABEL_KEYS and "image_check" in pipeline.LABEL_KEYS
app = (Path(__file__).resolve().parent.parent / "streamlit_app.py").read_text()
assert "image_review.problems(" in app
src = Path(pipeline.__file__).read_text()
assert src.count("image_review.review(") >= 3          # 終端機、瀏覽器、舊貼文補看
print("4. 檢查結果跟著草稿走到已發佈的貼文;兩個審核介面都顯示同一份警告 ✅")

# --- 誰互動:沒人互動不用問 API;問不到是 None,不是空的 ---
calls = []
class P:
    def get_engagers(self, ref): calls.append(ref); return {"likers": ["did:a"], "reposters": [], "repliers": []}
pipeline.get_platform = lambda ch: P()
zero = pipeline._reading_from_counts("bluesky", "7d", {"like_count": 0}, post_ref="at://x")
assert zero["engagers"] == {"likers": [], "reposters": [], "repliers": []} and calls == []
one = pipeline._reading_from_counts("bluesky", "7d", {"like_count": 1}, post_ref="at://x")
assert one["engagers"]["likers"] == ["did:a"] and calls == ["at://x"]
class Boom:
    def get_engagers(self, ref): raise RuntimeError("rate limited")
pipeline.get_platform = lambda ch: Boom()
assert pipeline._reading_from_counts("bluesky", "7d", {"like_count": 1}, post_ref="at://x")["engagers"] is None
print("5. 成效紀錄附上誰互動;0 互動不打 API;問不到記成 None(未知),數字照樣記 ✅")
print("\nall passed")
