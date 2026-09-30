import os, shutil, sys
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)

from contentmaster import image_generator as ig, pipeline, draft_queue

assert ig.image_format(b"\x89PNG\r\n\x1a\n") == "png"
assert ig.image_format(b"\xff\xd8\xff\xe0\x00\x10JFIF") == "jpg"
assert ig.image_format(b"fake-image-bytes") is None
assert ig.image_format(b"") is None
assert ig.image_format(None) is None
print("1. 影像格式辨識:PNG/JPEG 認得,假資料回 None ✅")

# generate_image refuses non-image bytes
import base64
class Resp:
    status_code=200
    def raise_for_status(self): pass
    def json(self): return {"success":True,"result":{"image":base64.b64encode(b"not an image").decode()}}
ig.requests.post = lambda *a, **k: Resp()
from contentmaster.config import settings
if settings.cloudflare.configured:
    assert ig.generate_image("x") is None
    print("2. Cloudflare 回非影像內容 -> 回 None,不寫檔 ✅")
else:
    print("2. (Cloudflare 未設定,跳過)")

# the written file is named for its real format
ig.generate_image = lambda p: b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"x"*100
drafts=[{"id":"d1","channel":"bluesky","text":"Cats pick where they feel safe here.",
         "brief":"b","source":"topics","characteristics":[]}]
pipeline._run_streamlit("P","bluesky",drafts)
e=draft_queue._latest_entries()["d1"]
assert e["image_path"].endswith(".jpg"), e["image_path"]
print("3. JPEG 內容存成 .jpg,不再一律叫 .png ✅")
print("\nall passed")
