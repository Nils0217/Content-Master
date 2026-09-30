import os, shutil, sys, json
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)
from contentmaster import topic_index

doc = R/"w.rtf"; doc.write_text(r"{\rtf1\ansi A guide about cats.}")
h = topic_index.file_hash(doc)
# an index that predates the category field, hash unchanged
topic_index.save_index("P", {"product":"P","source_hash":h,"topics":[{"topic":"T","brief":"b","used_count":0}]})
assert topic_index.load_category("P") == ""

asked=[]
topic_index._review_category_interactive = lambda proposed: (asked.append(proposed), "a domestic cat")[1]
idx = topic_index.sync_topics("P", doc, lambda: (_ for _ in ()).throw(AssertionError("extraction must not run")),
                               extract_category_fn=lambda: "a domestic cat")
assert idx["category"] == "a domestic cat"
print("1. 雜湊沒變、索引已存在時也會補問 category(以前永遠問不到)✅")
assert topic_index.load_category("P") == "a domestic cat"
print("2. 補問後寫回索引,下次直接讀得到 ✅")

# a failed proposal still asks the human
asked.clear()
topic_index.save_index("Q", {"product":"Q","source_hash":h,"topics":[]})
topic_index._review_category_interactive = lambda proposed: (asked.append(proposed), "typed by hand")[1]
idx = topic_index.sync_topics("Q", doc, lambda: "", 
                               extract_category_fn=lambda: (_ for _ in ()).throw(RuntimeError("cognee down")))
assert idx["category"] == "typed by hand" and asked == [""]
print("3. Cognee 掛掉時提議失敗,仍然問人,不是靜默跳過 ✅")
print("\nall passed")
