import os, shutil, sys
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)
import contentmaster.pipeline as pl
from contentmaster.draft_generator import DraftGenerator
from contentmaster import draft_generator as dg

assert not hasattr(pl, "PLACEHOLDER_FEATURES")
print("1. PLACEHOLDER_FEATURES 完全移除 ✅")

src = Path("src/contentmaster").read_text() if False else None
import inspect
body = inspect.getsource(dg.DraftGenerator.draft_posts)
assert "Built for teams who ship fast" not in body
print("2. 罐頭模板文案已刪除 ✅")

# nothing to ground on -> no drafts at all, rather than an invented one
dg.requests.post = lambda *a, **k: (_ for _ in ()).throw(dg.requests.RequestException("llm down"))
out = DraftGenerator().draft_posts({"name": "Fluffy roommate", "features": []}, "bluesky",
                                    n=1, topics=[], context="")
assert out is None, out
print("3. 完全沒有依據時回 None,不再產出杜撰的草稿 ✅")

# and with features supplied, nothing invents extras
from contentmaster.pipeline import _fallback_context
ctx = _fallback_context("Demo-white paper/cat.rtfd", "Fluffy roommate", [])
assert "compound memory" not in ctx and "Known features" not in ctx
assert "How to Use a Cat" in ctx
print("4. 沒給 --features 時 context 只有文件本身,不附加編造的特性 ✅")
print("\nall passed")
