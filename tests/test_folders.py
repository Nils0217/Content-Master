"""Folder selection for source documents and external data."""
import os, shutil, sys
from pathlib import Path
R = Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"] = str(R)

from contentmaster import folders

assert folders.get("whitepapers").name == "Demo-white paper"
assert folders.get("external").name == "seeds"
print("1. 沒設定時用預設資料夾 ✅")

papers = R/"papers"; papers.mkdir()
(papers/"one.pdf").write_text("x")
folders.set_folder("whitepapers", str(papers))
assert folders.get("whitepapers") == papers
assert [p.name for p in folders.documents()] == ["one.pdf"]
print("2. 設定後記住,並列出裡面的文件 ✅")

try:
    folders.set_folder("whitepapers", str(R/"does-not-exist"))
    print("FAIL"); sys.exit(1)
except NotADirectoryError:
    pass
assert folders.get("whitepapers") == papers, "失敗的設定不該蓋掉原本的"
print("3. 指向不存在的資料夾會被拒絕,原設定不變 ✅")

assert folders.resolve_document(None) == papers/"one.pdf"
assert folders.resolve_document("one.pdf") == papers/"one.pdf"
assert folders.resolve_document("nope.pdf") is None
print("4. 不給檔名時自動用唯一一份;給檔名時在資料夾內找 ✅")

(papers/"two.pdf").write_text("y")
assert folders.resolve_document(None) is None, "有兩份時不該替使用者挑"
assert folders.resolve_document("two.pdf") == papers/"two.pdf"
print("5. 資料夾有多份文件時不亂猜,要求明確指定 ✅")

ext = R/"ext"; ext.mkdir()
(ext/"trends.csv").write_text("a,b"); (ext/"notes.txt").write_text("ignored")
folders.set_folder("external", str(ext))
assert [p.name for p in folders.external_files()] == ["trends.csv"]
print("6. 外部資料資料夾只收 csv/tsv/json 這類 ✅")

# the whitepaper folder is what decides the Cognee dataset
from contentmaster.pipeline import dataset_for
assert dataset_for(papers/"one.pdf") == "papers"
assert dataset_for(ext/"trends.csv") == "ext"
print("7. 資料夾決定 Cognee 資料集,不是手打的產品名 ✅")
print("\nall passed")
