import os, shutil, sys
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)

from contentmaster import video_generator as vg, pipeline
from contentmaster.platforms.base import Platform, PlatformConstraints, PlatformAPIError
from contentmaster.platforms import registry

# 1. parser tolerates every separator shape the model actually emits
shapes = {
  "-- 在前(模型實際行為)":"-- \nSAY: A\nSHOW: B\n\n-- \nSAY: C\nSHOW: D",
  "--- 在後":"SAY: A\nSHOW: B\n---\nSAY: C\nSHOW: D",
  "完全沒分隔線":"SAY: A\nSHOW: B\nSAY: C\nSHOW: D",
}
for name, text in shapes.items():
    assert len(vg.parse_script(text)) == 2, name
print("1. 三種分隔線格式都解析得出 2 個 beat ✅")

# 2. half-made output is refused, not returned
try:
    vg.compose([vg.Beat(say="a", show="b")], R/"x.mp4")
    print("FAIL"); sys.exit(1)
except vg.VideoUnavailable:
    print("2. 沒有圖或音的 beat -> 拒絕輸出,不回半成品 ✅")

# 3. platform video limits
c = PlatformConstraints(max_video_seconds=600, max_video_bytes=300_000_000)
assert c.video_violations(660, 1000) and c.video_violations(10, 400_000_000)
assert not c.video_violations(11.4, 1_300_000)
print("3. 平台影片上限:超長/超大擋下,正常放行 ✅")

# 4. a platform with no video support says so instead of posting text only
sent = []
class NoVideo(Platform):
    name="bluesky"; constraints=PlatformConstraints(max_post_chars=300)
    def test_connection(self): return {}
    def publish_post(self, text, image=None, image_alt=""):
        sent.append("text"); return {"uri":"at://x"}
    def get_post_metrics(self, ref): return {}
registry.PLATFORMS["bluesky"]=NoVideo
draft={"id":"d1","channel":"bluesky","text":"Cats thrive on independence and control here."}
try:
    pipeline.step5_publish(draft, draft["text"], video=b"fake-video-bytes")
    print("FAIL — 應該要拒絕"); sys.exit(1)
except pipeline.PublishFailed:
    assert sent == [], "不得偷偷改發純文字"
print("4. adapter 不支援影片時明講,不會偷偷退成純文字 ✅")

# 5. a video-capable adapter gets the video, not publish_post
class WithVideo(NoVideo):
    def publish_video(self, text, video, video_alt=""):
        sent.append(("video", len(video), video_alt)); return {"uri":"at://v"}
registry.PLATFORMS["bluesky"]=WithVideo
sent.clear()
out = pipeline.step5_publish(draft, draft["text"], video=b"vid", video_alt="a cat")
assert sent == [("video", 3, "a cat")] and out["post_ref"]=="at://v"
print("5. 有影片時走 publish_video,alt 一起帶上 ✅")
print("\nall passed")
