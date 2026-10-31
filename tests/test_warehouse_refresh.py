import json, os, shutil, stat, sys, types
from datetime import datetime, timedelta, timezone
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)

import duckdb
from contentmaster import analysis, analysis_queue, audit, discuss, pipeline, scheduler, tracking_review, warehouse

REAL = Path(__file__).resolve().parent.parent / "warehouse"
DBT = Path(sys.executable).parent / "dbt"
PLAYS = R / "plays"
PLAYS.mkdir(); (R / "metrics").mkdir(); (R / "audit").mkdir(exist_ok=True)

# --- 真的跑 dbt:複製一份到測試資料夾,碰不到真實資料庫 ---
W = R / "warehouse"; W.mkdir()
for name in ("models", "seeds"):
    shutil.copytree(REAL / name, W / name)
for name in ("dbt_project.yml", "profiles.yml"):
    shutil.copy(REAL / name, W / name)

def history_row(post_id):
    return {"ts": "2026-10-20T10:00:00+00:00", "post_id": post_id, "product": "P", "channel": "bluesky",
            "checkpoint": "24h", "engagement_score": 1.0}

(PLAYS / "_history.jsonl").write_text(json.dumps(history_row("a")) + "\n")
for name in ("_failures", "_clusters", "_posts", "_reasoning"):
    (PLAYS / f"{name}.jsonl").write_text(json.dumps({"ts": "2026-10-20T10:00:00+00:00"}) + "\n")
(R / "metrics" / "post_metrics.jsonl").write_text(json.dumps({
    "post_id": "a", "channel": "bluesky", "recorded_at": "2026-10-20T10:00:00", "source": "test",
    "like_count": 0, "repost_count": 0, "reply_count": 0, "bookmark_count": 0, "quote_count": 0, "engagement_score": 0.0}) + "\n")
audit.log_event("test", "started")

import subprocess
env = {**os.environ, "DBT_PROFILES_DIR": str(W)}
seeded = subprocess.run([str(DBT), "--no-send-anonymous-usage-stats", "seed", "--no-use-colors", "--no-version-check"],
                        cwd=W, env=env, capture_output=True, text=True)
assert seeded.returncode == 0, seeded.stdout[-800:]

def rows():
    con = duckdb.connect(str(W / "local.duckdb"), read_only=True)
    try:
        return con.execute("select count(*) from performance_history where status = 'published'").fetchone()[0]
    finally:
        con.close()

warehouse.run_dbt(W, DBT)
assert rows() == 1
with (PLAYS / "_history.jsonl").open("a") as fh:
    fh.write(json.dumps(history_row("b")) + "\n")
assert rows() == 1, "還沒更新,資料庫不該自己變"
warehouse.run_dbt(W, DBT)
assert rows() == 2, "帳本多了一筆,更新後資料庫卻沒有"
print("1. 真的跑 dbt:帳本新增的貼文,更新後就進到分析讀的歷史 ✅")

# --- 失敗要說出來,而且記進錯誤清單 ---
def script(name, body):
    path = R / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path

def must_fail(fn, *args, contains):
    try:
        fn(*args)
    except warehouse.WarehouseFailed as exc:
        assert contains in str(exc), str(exc)
        return
    raise AssertionError(f"沒有失敗:{contains}")

broken = W / "models" / "staging" / "stg_broken.sql"
broken.write_text("select * from no_such_table_anywhere")
must_fail(warehouse.run_dbt, W, DBT, contains="no_such_table_anywhere")
broken.unlink()
must_fail(warehouse.run_dbt, W, R / "no-such-dbt", contains="could not start")
warehouse.REFRESH_TIMEOUT_SECONDS = 1
must_fail(warehouse.run_dbt, W, script("slow-dbt", "exec sleep 30"), contains="longer than")
warehouse.REFRESH_TIMEOUT_SECONDS = 120
from contentmaster import error_index
found = [info for sig, info in error_index.error_counts().items() if sig.startswith("warehouse |")]
assert sum(info["count"] for info in found) == 3, found
print("2. 模型壞掉、dbt 不見、卡住:都明確失敗,原因留在 `contentmaster errors` ✅")

# --- 測試環境不能碰真實資料庫 ---
real_db = REAL / "local.duckdb"
before = real_db.stat().st_mtime_ns if real_db.exists() else None
calls = []
run_dbt = warehouse.run_dbt
warehouse.run_dbt = lambda *a, **k: calls.append(a)
warehouse.refresh()
assert calls == []
warehouse.run_dbt = run_dbt
assert (real_db.stat().st_mtime_ns if real_db.exists() else None) == before
print("3. 測試環境不會更新真實資料庫 ✅")

# --- 資料庫存在卻讀不了:不能當成「還沒建立」,用沒有歷史的方式分析 ---
real_settings = analysis.settings
(R / "elsewhere" / "warehouse").mkdir(parents=True)
(R / "elsewhere" / "warehouse" / "local.duckdb").write_bytes(b"this is not a database")
analysis.settings = types.SimpleNamespace(project_root=R / "elsewhere")
must_fail(analysis._query_history, "P", "bluesky", "24h", contains="could not read post history")
analysis.settings = types.SimpleNamespace(project_root=R / "nowhere")
assert analysis._query_history("P", "bluesky", "24h") is None, "真的還沒建立時,照舊是沒有歷史"
analysis.settings = real_settings
print("4. 資料庫讀不了就停下;只有真的還沒建立,才算沒有歷史 ✅")

# --- 手動 review 和每日自動 review:更新失敗就不分析,貼文繼續留著 ---
tracking_review.TRACKING_PATH.parent.mkdir(parents=True, exist_ok=True)
with tracking_review.TRACKING_PATH.open("a") as fh:
    fh.write(json.dumps({"post_id": "due", "product": "P", "channel": "bluesky", "post_ref": "at://due", "final_text": "x",
                         "ts": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()}) + "\n")

order, opened, notes = [], [], []
def fail_refresh():
    order.append("refresh")
    raise warehouse.WarehouseFailed("disk is full")
warehouse.refresh = fail_refresh
pipeline._pull_checkpoint_metrics = lambda *a: order.append("metrics") or {"engagement_score": 0.0}
pipeline.launch_streamlit = lambda: opened.append(1)
scheduler.notify = lambda m: notes.append(m) or True
discuss.model_server_up = lambda: True

assert pipeline.run_review("24h") == 1, "手動 review 失敗卻回報成功"
assert order == ["refresh"], "資料庫更新失敗,還是去抓數據分析了"
assert analysis_queue.find_pending() == [] and opened == []
assert [e["post_id"] for e in tracking_review.find_due("24h")] == ["due"]
print("5. 手動 review:更新失敗就停下並回報,不分析、不開頁面,貼文仍待處理 ✅")

order.clear()
assert scheduler.review_due() == 0
assert order == ["refresh"] and analysis_queue.find_pending() == []
assert len(notes) == 1 and "history" in notes[0]
assert [e["post_id"] for e in tracking_review.find_due("24h")] == ["due"]
assert scheduler._take_lock(scheduler.REVIEW_LOCK_PATH), "失敗後沒有放鎖,之後每天都跑不了"
scheduler._release_lock(scheduler.REVIEW_LOCK_PATH)
print("6. 每日自動 review:更新失敗就跳通知,不留分析,下次醒來再試 ✅")

# --- 成功時,每一篇分析之前都先更新,而且排在分析前面 ---
order.clear(); notes.clear()
warehouse.refresh = lambda: order.append("refresh")
real_analyze = pipeline.analyze_performance
class Stop(Exception):
    pass
def fake_analyze(*a, **k):
    order.append("analyze")
    raise Stop
pipeline.analyze_performance = fake_analyze
try:
    pipeline._pull_and_analyze(tracking_review.find_due("24h")[0], "24h")
    raise AssertionError("分析沒有被呼叫")
except Stop:
    pass
pipeline.analyze_performance = real_analyze
assert order == ["refresh", "metrics", "analyze"], order
print("7. 每篇分析之前都先更新資料庫,手動和自動走同一條路 ✅")
print("\nall passed")
