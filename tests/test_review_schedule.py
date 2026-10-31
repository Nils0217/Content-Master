import json, os, shutil, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)

from contentmaster import analysis_queue, cli, discuss, pipeline, scheduler, tracking_review

agent_before = scheduler.REVIEW_AGENT_PATH.read_bytes() if scheduler.REVIEW_AGENT_PATH.exists() else None

def track(post_id, age):
    tracking_review.TRACKING_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tracking_review.TRACKING_PATH.open("a") as fh:
        fh.write(json.dumps({"post_id": post_id, "product": "P", "channel": "bluesky", "post_ref": f"at://{post_id}",
                             "final_text": "x", "ts": (datetime.now(timezone.utc) - age).isoformat()}) + "\n")

track("day-old", timedelta(hours=25))
track("week-old", timedelta(days=8))
track("fresh", timedelta(hours=2))
for i in range(4):
    track(f"month-old-{i}", timedelta(days=40))

made, notes, opened = [], [], []
def fake_analyse(entry, checkpoint):
    made.append((entry["post_id"], checkpoint))
    analysis_queue.queue_analysis(entry["post_id"], checkpoint, entry, {}, {"product": "P"}, "strategy")
pipeline._run_review_streamlit = fake_analyse
scheduler.notify = lambda m: notes.append(m) or True
pipeline.launch_streamlit = lambda: opened.append(1)

# --- Ollama 沒開:什麼都不做、不留空分析、要講 ---
discuss.model_server_up = lambda: False
assert scheduler.review_due() == 0 and made == [] and analysis_queue.find_pending() == []
assert len(notes) == 1 and "Ollama" in notes[0]
assert scheduler._take_lock(scheduler.REVIEW_LOCK_PATH), "跑完沒有放鎖"
scheduler._release_lock(scheduler.REVIEW_LOCK_PATH)
print("1. Ollama 沒開:不做分析、不留空的分析、跳通知;下次醒來再試 ✅")

# --- 一次只做上限篇數,新的先做,不開瀏覽器 ---
notes.clear()
discuss.model_server_up = lambda: True
assert scheduler.review_due() == 3
assert made[0] == ("day-old", "24h") and len(made) == 3
assert {cp for _, cp in made} <= {"24h", "7d"}
assert opened == [], "排程跑分析時開了瀏覽器"
assert len(notes) == 1 and "contentmaster review" in notes[0] and "3" in notes[0]
print("2. 一次最多 3 篇,先做 24 小時;不開瀏覽器,做完跳通知 ✅")

# --- 已經在等人的不重做;積壓的隔天接著做 ---
assert scheduler.review_due() == 3
assert len(set(made)) == len(made) == 6, "同一篇同一階段做了兩次"
print("3. 等人決定的不會重做,積壓的下次接著做 ✅")

# --- 都做完了:不呼叫模型、不再通知 ---
while scheduler.review_due():
    pass
notes.clear(); total = len(made)
discuss.model_server_up = lambda: (_ for _ in ()).throw(AssertionError("沒事做還去問 Ollama"))
assert scheduler.review_due() == 0 and len(made) == total and notes == []
print("4. 沒有新東西:不碰模型、不跳通知 ✅")

# --- 人還是要自己決定;只有 7d/30d 在等,一般 review 也要開頁面 ---
assert all(e["status"] == "pending" for e in analysis_queue.find_pending())
assert all("24h" not in (r.get("checkpoints_done") or {}) for r in tracking_review.find_due("24h"))
only_later = [e for e in analysis_queue.find_pending() if e["checkpoint"] != "24h"]
assert only_later
real_due = tracking_review.find_due
tracking_review.find_due = lambda cp: []
pipeline.run_review("24h")
assert opened == [1], "7d 或 30d 在等,但 review 說沒事"
tracking_review.find_due = real_due
print("5. 排程不會替人確認;只剩 7 天或 30 天的分析在等,`review` 也會開審核頁 ✅")

# --- 同時跑:只有一個 ---
assert scheduler._take_lock(scheduler.REVIEW_LOCK_PATH)
assert scheduler.review_due() == 0
scheduler._release_lock(scheduler.REVIEW_LOCK_PATH)
print("6. 兩個排程同時跑:只有一個會做 ✅")

# --- Mac 設定;隔離資料夾不碰真的 launchd ---
pl = scheduler.review_agent_plist("18:30")
assert pl["StartCalendarInterval"] == [{"Hour": 18, "Minute": 30}] and pl["RunAtLoad"] is False
assert pl["Label"] == "com.contentmaster.review" and pl["ProgramArguments"][1].endswith("scripts/review_due.sh")
assert Path(pl["ProgramArguments"][1]).exists() and os.access(pl["ProgramArguments"][1], os.X_OK)
assert scheduler.schedule_review("18:30") == "skipped (isolated data root)"
assert scheduler.unschedule_review() == "skipped (isolated data root)"
assert scheduler.review_schedule() is None
try:
    scheduler.schedule_review("18:15"); raise AssertionError("不是半小時也收了")
except ValueError:
    pass
assert cli.main(["review", "--schedule", "18:15"]) == 1
assert cli.main(["review", "--schedule"]) == 0 and cli.main(["review", "--unschedule"]) == 0
after = scheduler.REVIEW_AGENT_PATH.read_bytes() if scheduler.REVIEW_AGENT_PATH.exists() else None
assert after == agent_before, "測試改到了真實的 Mac 排程"
print("7. `review --schedule` 預設 18:30;時間不對會擋;測試完全沒碰真實的排程設定 ✅")

app = (Path(__file__).resolve().parent.parent / "streamlit_app.py").read_text()
assert "scheduler.schedule_review(" in app and "scheduler.unschedule_review(" in app
print("8. 審核頁和指令列走同一個函式 ✅")
print("\nall passed")
