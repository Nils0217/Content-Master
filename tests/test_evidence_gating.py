import os, shutil, sys
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)

from contentmaster.analysis import _judge_prior_suggestion as judge_prior, AnalysisResult
from contentmaster.discuss import _proposal_prompt
from contentmaster.scoring import MIN_BASELINE_POSTS, MIN_BASELINE_EVENTS

# 1. the real bug: advice never followed is not judged
assert judge_prior("write about litter boxes", followed="", current_score=0,
                   last_score=1, has_baseline=True) == "unknown"
print("1. 建議未被採納 -> unknown,不再誤判為無效 ✅")

# 2. followed but no baseline is still unknown
assert judge_prior("x", followed="the statement opening", current_score=0,
                   last_score=1, has_baseline=False) == "unknown"
print("2. 有採納但沒基準線 -> unknown(n=1 不是證據)✅")

# 3. both present -> a real verdict
assert judge_prior("x", "opening", 3, 1, True) == "looks_effective"
assert judge_prior("x", "opening", 0, 1, True) == "looks_ineffective"
print("3. 採納 + 基準線都在 -> 才給判定 ✅")

def mk(hb):
    return AnalysisResult(product="P", channel="bluesky", checkpoint="24h", n_prior_posts=1,
        historical_avg_score=1.0, current_score=0.0, trend="insufficient_for_trend",
        prior_suggestion="", prior_suggestion_effectiveness="unknown",
        evidence="E", has_baseline=hb)

# 4. the advisors are asked a different question, not silenced
cold = _proposal_prompt("P", "bluesky", mk(False))
warm = _proposal_prompt("P", "bluesky", mk(True))
assert "what result would show you were WRONG" in cold
assert "do not describe any result as proven, improving or declining" in cold
assert "Trend:" not in cold and "Real performance analysis" not in cold
assert "Trend:" in warm and "what result would show you were WRONG" not in warm
print("4. 沒基準線 -> 問假設與證偽條件;有基準線 -> 問調整 ✅")
print("   顧問沒有被關掉 —— 冷啟動的 8 篇仍然有方向")

# 5. the gate reads the same constants as the win/lose gate
import contentmaster.analysis as A, inspect
src = inspect.getsource(A.analyze_performance)
assert "MIN_BASELINE_POSTS" in src and "MIN_BASELINE_EVENTS" in src
print(f"5. 證據門檻與勝敗閘門共用常數({MIN_BASELINE_POSTS} 篇 / {MIN_BASELINE_EVENTS} 次)✅")
print("\nall passed")
