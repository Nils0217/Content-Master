"""The 2026-10-30 code review, one check per finding. Each runs even if an
earlier one fails, so a broken fix names itself."""
import json, os, shutil, subprocess, sys, threading, time, traceback, types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)

from contentmaster import analysis, analysis_queue, clusters, draft_queue, hypothesis as hyp, metrics_store
from contentmaster import modiqo_play, pipeline, posts, publish_failure, scheduler, stickiness as stk, tracking_review
from contentmaster.platforms.base import PlatformAPIError
from contentmaster.platforms.bluesky import BlueskyPlatform

ROOT = Path(__file__).resolve().parent.parent
P, CH = "Fluffy roommate", "bluesky"
H = {"id": "hyp-q", "test": "include at least one question"}
results = []


def finding(title):
    def run(fn):
        try:
            fn()
            print(f"{title} ✅")
        except Exception:
            results.append(title)
            print(f"{title} ❌\n{traceback.format_exc()}")
    return run


def track(post_id, **fields):
    tracking_review.TRACKING_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {"post_id": post_id, "product": P, "channel": CH, "post_ref": f"at://{post_id}", "final_text": "x",
              "ts": (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(), "checkpoints_done": {},
              "deleted_at": None, **fields}
    with tracking_review.TRACKING_PATH.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


def reading(post_id, checkpoint, engagers, at):
    metrics_store.METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with metrics_store.METRICS_PATH.open("a") as fh:
        fh.write(json.dumps({"post_id": post_id, "channel": CH, "recorded_at": at, "checkpoint": checkpoint,
                             "engagers": {"likers": engagers, "reposters": [], "repliers": []}}) + "\n")


def published(post_id, at, product=P, cluster_id=1, experiment=False, kind="cluster"):
    posts.register(post_id, product, CH, cluster_id=cluster_id, kind=kind,
                   experiment=None if kind == "extra" else experiment, scheduled_for="2026-10-26")
    posts.update(post_id, status="published", published_at=at)


@finding("1. 確認舊分析不會蓋掉新的進度,也不會讓已刪除的貼文復活;重複確認只算一次")
def _():
    snapshot = track("t1")
    tracking_review.mark_checkpoint_done(snapshot, "24h")
    tracking_review.mark_checkpoint_done(snapshot, "7d")
    assert set(tracking_review._latest_entries()["t1"]["checkpoints_done"]) == {"24h", "7d"}
    first = tracking_review._latest_entries()["t1"]["checkpoints_done"]["24h"]
    rows = tracking_review.TRACKING_PATH.read_text().count("\n")
    tracking_review.mark_checkpoint_done(snapshot, "24h")
    assert tracking_review.TRACKING_PATH.read_text().count("\n") == rows, "重複完成又寫了一列"
    assert tracking_review._latest_entries()["t1"]["checkpoints_done"]["24h"] == first

    snapshot = track("t2")
    tracking_review.mark_deleted(snapshot)
    tracking_review.mark_checkpoint_done(snapshot, "24h")
    latest = tracking_review._latest_entries()["t2"]
    assert latest["deleted_at"] and "24h" in latest["checkpoints_done"]
    assert "t2" not in {r["post_id"] for r in tracking_review.find_refreshable()}
    tracking_review.mark_checkpoint_done(track("t2b"), "24h")
    tracking_review.mark_deleted(track("t2c", checkpoints_done={"7d": "x"}))
    assert "7d" in tracking_review._latest_entries()["t2c"]["checkpoints_done"]

    entry = track("t3")
    finals = []
    real = pipeline._finalize_analysis
    pipeline._finalize_analysis = lambda *a, **k: (finals.append(a[1]), tracking_review.mark_checkpoint_done(entry, a[1]))
    try:
        analysis_queue.queue_analysis("t3", "24h", entry, {}, {"product": P, "channel": CH, "checkpoint": "24h",
            "n_prior_posts": 0, "historical_avg_score": None, "current_score": 0.0, "trend": "no_history",
            "prior_suggestion": "", "prior_suggestion_effectiveness": "unknown", "evidence": ""}, "s")
        q = analysis_queue.find_pending_for("t3", "24h")
        pipeline.apply_analysis_decision(q, True, "")
        pipeline.apply_analysis_decision(q, True, "")           # 同一個頁面按第二次
        assert finals == ["24h"], "同一份分析被記錄了兩次"
    finally:
        pipeline._finalize_analysis = real


@finding("2. 真的同時搶鎖只有一個成功;程序死掉自動釋放")
def _():
    gate = R / "go"
    child = ("import os,sys,time\nfrom pathlib import Path\nfrom contentmaster import scheduler\n"
             "Path(sys.argv[1]).touch()\n"
             f"while not Path({str(gate)!r}).exists(): time.sleep(0.001)\n"
             "print(scheduler._take_lock(), flush=True)\ntime.sleep(1.5)\n")
    procs = []
    for i in range(6):
        procs.append(subprocess.Popen([sys.executable, "-c", child, str(R / f"ready{i}")], stdout=subprocess.PIPE,
                                      text=True, env=os.environ))
    deadline = time.time() + 60
    while not all((R / f"ready{i}").exists() for i in range(6)) and time.time() < deadline:
        time.sleep(0.02)
    gate.touch()
    answers = [p.communicate(timeout=60)[0].strip() for p in procs]
    assert answers.count("True") == 1, answers

    # Between processes the race is rare; threads released together hit it.
    for _ in range(300):
        barrier, won = threading.Barrier(8), []
        def race():
            barrier.wait()
            if scheduler._take_lock():
                won.append(1)
        threads = [threading.Thread(target=race) for _ in range(8)]
        for th in threads: th.start()
        for th in threads: th.join()
        assert len(won) == 1, f"{len(won)} 個同時拿到鎖"
        scheduler._release_lock()
    assert scheduler._take_lock(), "程序都結束了,鎖還沒放"
    scheduler._release_lock()
    assert scheduler._take_lock(scheduler.REVIEW_LOCK_PATH)
    assert scheduler._take_lock(), "發佈鎖和分析鎖不該互相擋"
    scheduler._release_lock(); scheduler._release_lock(scheduler.REVIEW_LOCK_PATH)


@finding("3. 發佈失敗的草稿留在審核清單,看得到原因,改過的文字還在")
def _():
    draft_queue.queue_draft("f1", P, CH, "Edited text?")
    draft_queue.update_entry("f1", status="publish_failed", failure_kind="technical", failure_reason="boom")
    assert "f1" in {e["draft_id"] for e in draft_queue.find_reviewable()}
    assert "f1" not in {e["draft_id"] for e in draft_queue.find_pending()}
    draft_queue.update_entry("f1", status="published")
    assert "f1" not in {e["draft_id"] for e in draft_queue.find_reviewable()}
    app = (ROOT / "streamlit_app.py").read_text()
    assert "draft_queue.find_reviewable()" in app and 'entry.get("failure_reason")' in app


class Fake(BlueskyPlatform):
    def __init__(self):
        super().__init__(cfg=types.SimpleNamespace(configured=True, handle="h", app_password="p"))
        self.sent, self.found, self.fail = [], None, None

    def publish_post(self, text, image=None, image_alt=""):
        if self.fail:
            raise self.fail
        self.sent.append(text)
        return {"uri": f"at://sent/{len(self.sent)}", "cid": "c"}

    def find_published(self, text):
        return self.found

    def account_snapshot(self):
        return {"followers": 0}


@finding("4. 網路斷線不會讓排程貼文失敗;不確定有沒有送出時,先查平台再決定")
def _():
    from atproto_client.exceptions import AtProtocolError, NetworkError
    from contentmaster.platforms.base import PlatformNetworkError

    real = BlueskyPlatform(cfg=types.SimpleNamespace(configured=True, handle="h", app_password="p"))
    real._client = types.SimpleNamespace(post=lambda text: (_ for _ in ()).throw(NetworkError()))
    try:
        real.publish_post("hello"); raise AssertionError("沒有丟出例外")
    except PlatformNetworkError as e:
        f = publish_failure.classify(e)
    assert f.kind == publish_failure.ENVIRONMENT and f.retry_unchanged and f.uncertain and "may" in f.explain()
    real._client = types.SimpleNamespace(post=lambda text: (_ for _ in ()).throw(AtProtocolError("no")))
    try:
        real.publish_post("hello"); raise AssertionError
    except PlatformAPIError as e:
        f = publish_failure.classify(e)
    assert f.kind == publish_failure.TECHNICAL and not f.retry_unchanged, "一般 API 錯誤不該自動重試"
    unsent = publish_failure.classify(PlatformNetworkError("down", may_have_sent=False))
    assert unsent.retry_unchanged and not unsent.uncertain

    platform = Fake()
    get_platform = pipeline.get_platform
    pipeline.get_platform = lambda ch: platform
    try:
        clusters.create(P, CH, date(2030, 1, 7), H, 0)
        slots = clusters.slots(1, date(2030, 1, 7))
        ids = []
        for i, sl in enumerate(slots[:3]):
            pid = posts.new_post_id(); ids.append(pid)
            posts.register(pid, P, CH, cluster_id=1, kind="cluster", experiment=sl["experiment"], scheduled_for=sl["date"])
            draft_queue.queue_draft(pid, P, CH, f"A real post about cats, number {i}?", labels={
                "cluster_id": 1, "kind": "cluster", "experiment": sl["experiment"], "scheduled_for": sl["date"]})
            posts.approve(pid)
            posts.update(pid, status="scheduled", scheduled_at=datetime(2030, 1, 7, 18, i).astimezone().isoformat())
        now = datetime(2030, 1, 7, 18, 0, 30).astimezone()
        a, b, c = ids

        platform.fail = PlatformNetworkError("reset", may_have_sent=True)
        assert scheduler.publish_due(now) == 0
        assert posts.get(a)["status"] == "scheduled", "暫時的網路問題讓貼文變成失敗"
        assert draft_queue._latest_entries()[a]["send_uncertain"] is True

        platform.fail, platform.found = None, {"uri": "at://already/live", "cid": "c"}
        assert scheduler.publish_due(now) == 1
        assert platform.sent == [], "其實已經發出去,卻又發一次"
        assert posts.get(a)["status"] == "published" and posts.get(a)["post_ref"] == "at://already/live"

        later = datetime(2030, 1, 7, 18, 1, 30).astimezone()
        platform.found, platform.fail = None, PlatformNetworkError("reset", may_have_sent=True)
        scheduler.publish_due(later)
        platform.fail = None
        assert scheduler.publish_due(later) == 1 and len(platform.sent) == 1, "查過沒發出去,應該要補發"

        last = datetime(2030, 1, 7, 18, 2, 30).astimezone()
        platform.fail = KeyboardInterrupt()                     # 送出途中程序被殺
        try:
            scheduler.publish_due(last)
        except KeyboardInterrupt:
            pass
        platform.fail = None
        platform.found = {"uri": "at://c/live", "cid": "c"}
        assert draft_queue._latest_entries()[c]["send_uncertain"] is True, "送出前沒留下記號"
        assert scheduler.publish_due(last) == 1 and posts.get(c)["post_ref"] == "at://c/live"
        assert len(platform.sent) == 1, "程序中斷後又重發"

        platform.found = None
        platform.find_published = lambda text: (_ for _ in ()).throw(PlatformNetworkError("still down", may_have_sent=False))
        draft = {"id": "z", "channel": CH}
        try:
            pipeline.step5_publish(draft, "A real post about cats, final?", check_first=True, acknowledged=True)
            raise AssertionError("查不到平台時不該直接送出")
        except pipeline.PublishFailed as e:
            assert e.failure.retry_unchanged and len(platform.sent) == 1
    finally:
        pipeline.get_platform = get_platform


@finding("5. 建議有沒有效,看的是這篇貼文有沒有照做,不是上一篇")
def _():
    history = [{"engagement_score": 1.0, "engagement_events": 3, "improvement_note": "TEST: include at least one question",
                "evidence_used": "older post evidence"} for _ in range(10)]
    real = analysis._query_history
    analysis._query_history = lambda *a, **k: history
    try:
        def verdict(**k):
            return analysis.analyze_performance(P, CH, 5.0, **k).prior_suggestion_effectiveness
        assert verdict() == "unknown", "這篇沒有證據,卻拿上一篇的來判定"
        assert verdict(current_evidence="Step 1: Establish Trust") == "unknown", "不相干的內容不算照做"
        assert verdict(current_evidence="none") == "unknown"
        assert verdict(current_hypothesis="include at least one question") == "looks_effective"
        assert verdict(current_evidence="include at least one question") == "looks_effective"
    finally:
        analysis._query_history = real
    assert "current_evidence=entry.get" in (ROOT / "src/contentmaster/pipeline.py").read_text()


@finding("6. 比較時用同一個檢查點的讀數,之後才發生的互動不算回頭客")
def _():
    published("s-old", "2026-01-01T00:00:00+00:00", cluster_id=50)
    published("s-new", "2026-01-05T00:00:00+00:00", cluster_id=50)
    reading("s-old", "24h", ["early"], "2026-01-02T00:00:00+00:00")
    reading("s-new", "24h", ["early", "late"], "2026-01-06T00:00:00+00:00")
    reading("s-new", "30d", ["a", "b", "c", "d"], "2026-02-05T00:00:00+00:00")
    reading("s-old", "refresh", ["early", "late"], "2026-02-01T00:00:00+00:00")
    assert stk.engagers_of("s-new", checkpoint="24h") == {"early", "late"}
    assert stk.engagers_of("s-new") == {"a", "b", "c", "d"}
    assert stk.engagers_of("s-old", as_of="2026-01-10T00:00:00+00:00") == {"early"}
    assert stk.engagers_of("s-new", checkpoint="7d") is None
    s = stk.stickiness("s-new", P, CH, checkpoint="24h")
    assert (s["engagers"], s["returning"]) == (2, 1), s
    result = hyp.evaluate(H, "s-new", P, CH, [], None, "24h")
    assert result["engagers"] == 2, "evaluate 沒有把檢查點傳下去"


@finding("7. 一週的追蹤變化只算到週日午夜,用當地時間")
def _():
    def utc(*a):
        return datetime(*a).astimezone().astimezone(timezone.utc).isoformat(timespec="seconds")
    stk.ACCOUNT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with stk.ACCOUNT_PATH.open("a") as fh:
        for at, n in ((utc(2031, 3, 3, 0, 5), 10), (utc(2031, 3, 9, 23, 55), 12), (utc(2031, 3, 10, 0, 0), 30)):
            fh.write(json.dumps({"ts": at, "channel": "week", "source": "t", "followers": n}) + "\n")
    start = datetime(2031, 3, 3).astimezone().isoformat()
    end = datetime(2031, 3, 10).astimezone().isoformat()
    assert stk.follower_change("week", start, end)["change"] == 2
    assert hyp.week_window("2031-03-03") == (start, end)
    source = (ROOT / "src/contentmaster/hypothesis.py").read_text()
    assert "timedelta(days=8)" not in source and "week_window(c[" in source


@finding("8. 假設的對照組只來自同產品、同一週的 experiment 貼文")
def _():
    clusters.create(P, CH, date(2032, 1, 5), H, 0)
    cid = max(c["cluster_id"] for c in clusters.for_product(P, CH))
    published("main", "2032-01-05T18:00:00+00:00", cluster_id=cid)
    published("main2", "2032-01-06T18:00:00+00:00", cluster_id=cid)
    published("ctrl1", "2032-01-05T18:00:00+00:00", cluster_id=cid, experiment=True)
    published("ctrl2", "2032-01-08T18:00:00+00:00", cluster_id=cid, experiment=True)
    posts.register("ctrl3", P, CH, cluster_id=cid, kind="cluster", experiment=True, scheduled_for="2032-01-09")
    published("extra", "2032-01-08T19:00:00+00:00", cluster_id=cid, kind="extra")
    published("old-week", "2031-12-01T18:00:00+00:00", cluster_id=cid - 1, experiment=True)
    published("foreign", "2032-01-05T18:00:00+00:00", product="Other", cluster_id=99, experiment=True)
    assert sorted(hyp.comparison_posts("main", P, CH, "24h", "hyp-q")) == ["ctrl1", "ctrl2"]

    with modiqo_play.HISTORY_PATH.open("a") as fh:
        for pid, product in (("legacy-a", P), ("legacy-foreign", "Other")):
            fh.write(json.dumps({"product": product, "channel": CH, "checkpoint": "24h", "post_id": pid,
                                 "metrics": {"post_id": pid, "like_count": 1}}) + "\n")
    assert hyp.comparison_posts("legacy-a2", P, CH, "24h", "hyp-q") == ["legacy-a"], "舊貼文也不能混入別的產品"


@finding("9. 測試結束碼不是 0 就算失敗,就算最後印了 all passed")
def _():
    def runner(body):
        box = R / "runner"
        shutil.rmtree(box, ignore_errors=True)
        (box / "scripts").mkdir(parents=True); (box / "tests").mkdir(); (box / ".venv/bin").mkdir(parents=True)
        shutil.copy(ROOT / "scripts/run_tests.sh", box / "scripts/run_tests.sh")
        (box / ".venv/bin/python").symlink_to(sys.executable)
        (box / "tests/test_x.py").write_text(body)
        return subprocess.run(["bash", str(box / "scripts/run_tests.sh"), str(box / "scratch")],
                              capture_output=True, text=True, env={**os.environ, "CONTENTMASTER_DATA_ROOT": ""})
    assert runner("print('all passed')").returncode == 0
    assert runner("import sys\nprint('all passed')\nsys.exit(3)").returncode != 0, "結束碼被忽略"
    assert runner("print('done')").returncode != 0, "沒有印完成標記也算過"


if results:
    print(f"\n{len(results)} finding(s) still broken")
    sys.exit(1)
print("\nall passed")
