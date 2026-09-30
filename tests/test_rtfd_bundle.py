import os, shutil, sys
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)
from contentmaster.document import read_text, resolve_bundle
from contentmaster.topic_index import file_hash

# build a fake .rtfd bundle
b = R/"doc.rtfd"; b.mkdir()
(b/"TXT.rtf").write_text(r"{\rtf1\ansi Hello from inside the bundle.}")
(b/"pic.webp").write_bytes(b"RIFF fake image")

assert resolve_bundle(b) == b/"TXT.rtf"
print("1. .rtfd 目錄解析到裡面的 TXT.rtf ✅")
assert "Hello from inside the bundle" in read_text(b)
print("2. read_text 讀得出 bundle 內容 ✅")
h1 = file_hash(b)
(b/"pic.webp").write_bytes(b"RIFF different image entirely")
assert file_hash(b) == h1
print("3. 換圖片不改變雜湊(只有文字算數)✅")
(b/"TXT.rtf").write_text(r"{\rtf1\ansi Different words now.}")
assert file_hash(b) != h1
print("4. 改文字才觸發重新抽取 ✅")

plain = R/"plain.rtf"; plain.write_text(r"{\rtf1\ansi Plain file.}")
assert resolve_bundle(plain) == plain and "Plain file" in read_text(plain)
print("5. 一般 .rtf 行為不變 ✅")
assert read_text(R/"nope.rtfd") == ""
print("6. 不存在的路徑回空字串,不丟例外 ✅")
print("\nall passed")
