import os, shutil, sys, json
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)

from contentmaster import pipeline, image_generator, draft_queue
from contentmaster.draft_generator import _subject_description

# 1. the prompt no longer asserts a product
d = _subject_description("Fluffy roommate", "a domestic cat")
assert "SUBJECT: a domestic cat" in d
assert "product" not in d.split("Do not invent")[0].lower()
assert "Do not invent a product" in d
print("1. category 主導,不再宣稱有產品要賣 ✅")

# 2. no category -> says so rather than assuming
d2 = _subject_description("Fluffy roommate", "")
assert "do not assume it is a product for sale" in d2
print("2. 沒有 category 時明說未知,不替它假設 ✅")

# 3. --no-image skips generation entirely
calls = []
image_generator.generate_image = lambda p: calls.append(p) or b"x"
drafts = [{"id": "d1", "channel": "bluesky", "text": "Cats pick where they feel safe.",
           "brief": "b", "source": "topics", "characteristics": ["statement"]}]
pipeline._run_streamlit("P", "bluesky", list(drafts), no_image=True)
assert calls == [], f"不該呼叫產圖,實際 {len(calls)} 次"
print("3. --no-image:完全沒有呼叫產圖 ✅")

e = draft_queue._latest_entries()["d1"]
assert e.get("image_path") is None
assert "no image" in (e.get("characteristics") or [])
print("4. 記成 'no image' 特點,之後比較得出來 ✅")

# 5. without the flag it still generates
shutil.rmtree(R); R.mkdir()
calls.clear()
pipeline._run_streamlit("P", "bluesky", list(drafts), no_image=False)
assert len(calls) == 1
print("5. 不加旗標時行為不變 ✅")
print("\nall passed")
