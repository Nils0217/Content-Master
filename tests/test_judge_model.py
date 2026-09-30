import os, shutil, sys
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)

import contentmaster.discuss as d
from contentmaster.discuss import _clean_synthesis

# 1. a real third family judges
assert d._JUDGE_MODEL == "qwen3:4b"
assert d._JUDGE_ENABLED is True
fams = {d._MODEL_A.split(":")[0], d._MODEL_B.split(":")[0], d._JUDGE_MODEL.split(":")[0]}
assert len(fams) == 3, fams
print(f"1. 三個不同家族:{sorted(fams)} ✅")

# 2. reasoning monologue is stripped, not pasted downstream
assert _clean_synthesis("<think>" + "musing " * 500 + "</think>Pick A.") == "Pick A."
assert _clean_synthesis("<think>" + "x" * 9000) is None
assert _clean_synthesis("y" * 2000) is None
print("2. <think> 區塊剝除;未閉合或過長 -> None(退回兩份提案)✅")

# 3. model ids stay out of the judge prompt
import inspect
src = inspect.getsource(d.synthesize_strategy)
assert "Advisor A: {proposal_a}" in src
assert "({_MODEL_A})" not in src
print("3. 裁判 prompt 不含模型 ID ✅")

# 4. a failed synthesis still returns something usable
calls = []
d._complete = lambda m, p, timeout=90: calls.append(m) or ("A says x" if m != "qwen3:4b" else None)
from contentmaster.analysis import AnalysisResult
a = AnalysisResult(product="P", channel="bluesky", checkpoint="24h", n_prior_posts=0,
    historical_avg_score=None, current_score=0.0, trend="no_history",
    prior_suggestion="", prior_suggestion_effectiveness="unknown", evidence="E")
out = d.synthesize_strategy("P", "bluesky", a)
assert out.startswith("Advisor A:") and "Advisor B:" in out
assert "qwen3:4b" not in out and "llama" not in out
print("4. 裁判失敗時退回兩份提案,且不洩漏模型 ID ✅")
print("\nall passed")
