import os, shutil, sys, types
from datetime import date, datetime
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)

from contentmaster import scheduler, clusters, posts, draft_queue, pipeline

P, CH = "Fluffy roommate", "bluesky"
clusters.create(P, CH, date(2026, 10, 26), {"id": "hyp-q", "test": "include at least one question"}, 0)
slots = clusters.slots(1, date(2026, 10, 26))
(R / "generated_images").mkdir(); (R / "generated_videos").mkdir()
(R / "generated_images" / "moved.jpg").write_bytes(b"IMAGE")
(R / "generated_videos" / "moved.mp4").write_bytes(b"VIDEO")
OLD = "/Users/someone/Desktop/Marketing hack"           # 專案搬家前存下的絕對路徑

def queue(slot, image_path=None, **labels):
    pid = posts.new_post_id()
    posts.register(pid, P, CH, cluster_id=1, kind="cluster", experiment=slot["experiment"],
                   scheduled_for=slot["date"])
    draft_queue.queue_draft(pid, P, CH, f"Post for {slot['date']}?", image_path=image_path, labels={
        "cluster_id": 1, "kind": "cluster", "experiment": slot["experiment"],
        "scheduled_for": slot["date"], **labels})
    posts.approve(pid)
    return pid

moved_image = queue(slots[0], f"{OLD}/generated_images/moved.jpg")
moved_video = queue(slots[1], f"{OLD}/generated_images/moved.jpg", video_path=f"{OLD}/generated_videos/moved.mp4",
                    video_alt="A cat stretching")
no_image = queue(slots[2])
gone_image = queue(slots[3], f"{OLD}/generated_images/gone.jpg")
gone_video = queue(slots[4], video_path=f"{OLD}/generated_videos/gone.mp4")
scheduler.schedule_cluster(P, CH, 1, "18:00")

sent, alts = {}, {}
def fake_publish(draft, text, image=None, image_alt="", video=None, video_alt="", original_text=None,
                 acknowledged=False, check_first=False):
    sent[draft["id"]] = (image, video)
    alts[draft["id"]] = video_alt
    return {**draft, "text": text, "status": "published", "post_ref": f"at://{draft['id']}"}
pipeline.step5_publish = fake_publish
pipeline.get_platform = lambda ch: types.SimpleNamespace(account_snapshot=lambda: {"followers": 0})

assert scheduler.publish_due(datetime(2026, 10, 31, 19, 0).astimezone()) == 3

# --- 專案搬家後,圖還在新位置:照樣帶圖發 ---
assert sent[moved_image] == (b"IMAGE", None), "圖片還在,卻沒有帶上"
assert sent[moved_video] == (None, b"VIDEO"), "影片還在,卻沒有帶上"
assert alts[moved_video] == "A cat stretching", "排程影片少了替代文字"
print("1. 專案搬家後,舊的絕對路徑仍找得到新位置的圖和影片,排程貼文照樣帶上 ✅")

# --- 審核頁讀的也是同一份資料,看得到圖 ---
entry = draft_queue._latest_entries()[moved_image]
assert Path(entry["image_path"]).read_bytes() == b"IMAGE"
print("2. 審核頁和分析讀到的路徑也已經對齊新位置 ✅")

# --- 本來就沒有圖的貼文照常發 ---
assert sent[no_image] == (None, None) and posts.get(no_image)["status"] == "published"
print("3. 本來就沒有圖的貼文,照常只發文字 ✅")

# --- 核准時有圖、發佈時圖不見了:不能默默發成純文字 ---
for pid in (gone_image, gone_video):
    assert pid not in sent, "核准時有圖,發佈時找不到,卻還是發了純文字"
    assert posts.get(pid)["status"] == "failed"
    e = draft_queue._latest_entries()[pid]
    assert e["status"] == "publish_failed" and e["failure_kind"] == "media_missing"
    assert "gone" in e["failure_reason"]
print("4. 核准的圖或影片真的不見了:不發佈、標成失敗、記下缺哪個檔案 ✅")

# --- 草稿有圖的描述,但實際發出去沒有圖:分析不能當作有圖 ---
note = pipeline._image_note({"image_description": "A cat on a sofa",
                             "environment": {"has_image": False, "has_video": False}})
assert note == "The post had no image.", note
note = pipeline._image_note({"image_description": "A cat on a sofa",
                             "environment": {"has_image": True, "has_video": False}})
assert "A cat on a sofa" in note, note
print("5. 分析看的是實際發出去的內容:沒帶圖的貼文不會被描述成有圖 ✅")

# --- 圖已經不見,核准當下就要說,不能等到發佈才失敗 ---
last = queue(slots[5], f"{OLD}/generated_images/also-gone.jpg")
posts.update(last, status="draft")
try:
    pipeline.approve_for_schedule(last, "Does your cat come to you on its own?", acknowledged=True)
    raise AssertionError("圖不見了還能核准")
except pipeline.NotSchedulable as e:
    assert "also-gone" in str(e)
print("6. 圖已經不見的排程貼文,核准當下就被擋下,勾選確認也不能略過 ✅")
print("\nall passed")
