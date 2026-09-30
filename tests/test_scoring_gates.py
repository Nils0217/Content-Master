import os, json, shutil, sys
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); (R/"plays").mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)

from contentmaster import modiqo_play, scoring

H = R/"plays"/"_history.jsonl"
def row(ch, cp, **c):
    return json.dumps({"channel":ch,"checkpoint":cp,"metrics":{"post_id":f"p{c}","like_count":0,
        "repost_count":0,"reply_count":0,"bookmark_count":0, **c}})
lines=[row("bluesky","24h",like_count=i%3) for i in range(12)]
lines += [row("bluesky","7d",like_count=99), row("x","24h",like_count=99)]
lines.append(json.dumps({"channel":"bluesky","checkpoint":"24h","metrics":{"impressions":1,"ctr":1.0}}))
H.write_text("\n".join(lines)+"\n")

base = modiqo_play.comparable_history("bluesky","24h")
assert len(base)==12, len(base)
print("1. 基準線只取同 channel、同 checkpoint、有原始計數的列 (12) ✅")

assert scoring.judge({"like_count":1}, base).state == "likes_only"
print("2. 1 個讚 -> likes_only ✅")
v = scoring.judge({"repost_count":1,"like_count":1}, base)
assert v.state == "proven_tentative", v.state
print(f"3. 1 轉發+1 讚 -> {v.state} (score {v.score:g} > p75 {v.baseline_p75:g}) ✅")
assert scoring.judge({"like_count":1}, base[:5]).state == "hypothesis"
print("4. 基準線只有 5 篇 -> hypothesis(閘門 1 擋下)✅")
assert scoring.judge({"like_count":1}, [{"like_count":0} for _ in range(12)]).state == "hypothesis"
print("5. 12 篇全 0 + 1 個讚 -> hypothesis(舊門檻在此判成功)✅")
assert scoring.judge({"repost_count":1}, base+base[:8]).state == "proven"
print("6. 基準線 20 篇 -> proven(不再 _tentative)✅")

assert scoring.engagement_score({"like_count":5}) == 5.0
assert scoring.engagement_score({"repost_count":1}) == 3.0
assert scoring.engagement_score({"bookmark_count":1}) == 3.0
print("7. 加權總和:5 讚=5、1 轉發=3、1 收藏=3(舊公式全是 1.0)✅")
assert scoring.control_arm_size(8) == 2 and scoring.control_arm_size(12) == 3
print("8. 對照組由常數推導:8→2、12→3,比例不低於 20% ✅")
print("\nall passed")
