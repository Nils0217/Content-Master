import os, shutil, sys, json, types
from datetime import date, datetime, timedelta
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)

from contentmaster import scheduler, clusters, posts, draft_queue, pipeline

agent_before = scheduler.AGENT_PATH.read_bytes() if scheduler.AGENT_PATH.exists() else None
P, CH = "Fluffy roommate", "bluesky"
H = {"id": "hyp-q", "test": "include at least one question"}
c = clusters.create(P, CH, date(2026, 10, 26), H, 0)
ids = {}
for sl in clusters.slots(1, date(2026, 10, 26)):
    pid = posts.new_post_id(); ids[sl["date"]] = pid
    posts.register(pid, P, CH, cluster_id=1, kind="cluster", experiment=sl["experiment"],
                   scheduled_for=sl["date"])
    draft_queue.queue_draft(pid, P, CH, f"Post for {sl['date']}?", labels={
        "cluster_id": 1, "kind": "cluster", "experiment": sl["experiment"], "scheduled_for": sl["date"]})
for d in ("2026-10-26", "2026-10-27", "2026-10-28"):
    posts.approve(ids[d])

# --- 排程:一個時間,套到整週 ---
try:
    scheduler.schedule_cluster(P, CH, 1, "18:15"); raise AssertionError("不是半小時也收了")
except ValueError:
    pass
res = scheduler.schedule_cluster(P, CH, 1, "18:00")
assert res["scheduled"] == ["2026-10-26", "2026-10-27", "2026-10-28"]
assert res["not_approved"] == ["2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"]
mon = posts.get(ids["2026-10-26"])
at = datetime.fromisoformat(mon["scheduled_at"])
assert mon["status"] == "scheduled" and (at.hour, at.minute, at.date()) == (18, 0, date(2026, 10, 26))
assert clusters.get(P, CH, 1)["post_time"] == "18:00"
print("1. 整週一個發文時間:核准過的排到各自那天 18:00;還沒核准的列出來;不是半小時的時間不收 ✅")

# 排程之後才核准的,自動加入同一個時間(走真正的核准函式)
row = pipeline.approve_for_schedule(ids["2026-10-29"], "Does your cat come to you on its own?")
assert row["status"] == "scheduled" and datetime.fromisoformat(row["scheduled_at"]).hour == 18
print("2. 排程之後才核准的貼文,自動排到同一個時間,不用再按一次 ✅")

# --- 時間到才發;發出去的記錄晚了多久 ---
sent = []
def fake_publish(draft, text, image=None, image_alt="", video=None, video_alt="", original_text=None,
                 acknowledged=False):
    assert acknowledged is True, "核准時已經檢查過,排程發佈要帶 acknowledged"
    sent.append((draft["id"], text))
    return {**draft, "text": text, "status": "published", "post_ref": f"at://{draft['id']}"}
pipeline.step5_publish = fake_publish
pipeline.get_platform = lambda ch: types.SimpleNamespace(account_snapshot=lambda: {"followers": 0})
tue_evening = datetime(2026, 10, 27, 18, 40).astimezone()
assert [r["scheduled_for"] for r in scheduler.due(tue_evening)] == ["2026-10-26", "2026-10-27"]
assert scheduler.publish_due(tue_evening) == 2
assert [t for _, t in sent] == ["Post for 2026-10-26?", "Post for 2026-10-27?"]
mon, tue = posts.get(ids["2026-10-26"]), posts.get(ids["2026-10-27"])
assert mon["status"] == tue["status"] == "published"
assert mon["late_minutes"] == 24 * 60 + 40 and tue["late_minutes"] == 40
assert draft_queue._latest_entries()[ids["2026-10-27"]]["status"] == "published"
assert posts.get(ids["2026-10-28"])["status"] == "scheduled"           # 還沒到
assert scheduler.publish_due(tue_evening) == 0                           # 不會重發
print("3. 只發時間已到的;Mac 睡著錯過的醒來補發,記下晚了幾分鐘;發過的不會再發 ✅")

# --- 發佈失敗:網路問題留著下次再試;內容問題標失敗 ---
def failing(kind, retry):
    def f(*a, **k):
        raise pipeline.PublishFailed(types.SimpleNamespace(kind=kind, reason="x", retry_unchanged=retry,
                                                           explain=lambda: "x"), "id")
    return f
wed_evening = datetime(2026, 10, 28, 19, 0).astimezone()
pipeline.step5_publish = failing("network", True)
assert scheduler.publish_due(wed_evening) == 0 and posts.get(ids["2026-10-28"])["status"] == "scheduled"
pipeline.step5_publish = failing("too_long", False)
assert scheduler.publish_due(wed_evening) == 0 and posts.get(ids["2026-10-28"])["status"] == "failed"
print("4. 網路 / 流量限制:留在排程裡,下次醒來再試;平台拒絕內容:標成失敗,不會一直重試 ✅")

# --- 同時跑兩次,只有一個會發 ---
scheduler.LOCK_PATH.write_text(str(os.getpid()))
assert scheduler.publish_due(wed_evening) == 0
scheduler.LOCK_PATH.write_text("999999")                                 # 死掉的舊鎖
pipeline.step5_publish = fake_publish
assert scheduler.publish_due(datetime(2026, 10, 29, 19, 0).astimezone()) == 1
print("5. 同時跑兩個發佈:只有一個會發;程序死掉留下的舊鎖不會卡住 ✅")

# --- Mac 的排程設定 ---
pl = scheduler.agent_plist(["18:00", "09:30"])
assert pl["StartCalendarInterval"] == [{"Hour": 9, "Minute": 30}, {"Hour": 18, "Minute": 0}]
assert pl["ProgramArguments"][-1] == "publish-due" and pl["ProgramArguments"][0].endswith("/contentmaster")
assert pl["RunAtLoad"] is False and pl["Label"] == "com.contentmaster.publish"
assert scheduler.sync_agent() == "skipped (isolated data root)"
after = scheduler.AGENT_PATH.read_bytes() if scheduler.AGENT_PATH.exists() else None
assert after == agent_before, "測試改到了真實的 Mac 排程"
print("6. Mac 只在發文時間叫醒(每個時間一個),不是每幾分鐘檢查;測試完全沒碰真實的排程設定 ✅")

app = (Path(__file__).resolve().parent.parent / "streamlit_app.py").read_text()
cli = (Path(pipeline.__file__).parent / "cli.py").read_text()
assert "scheduler.schedule_cluster(" in app and '"Schedule post"' in app and "scheduler.schedule_cluster(" in cli
assert "Fluffy roommate" not in cli
print("7. 審核頁的 Schedule 按鈕和 `contentmaster schedule` 走同一個函式;程式裡沒有寫死產品名 ✅")
print("\nall passed")
