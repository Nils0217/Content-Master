import os, shutil, sys
from pathlib import Path
R=Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"]=str(R)

from contentmaster import product_assets as pa, topic_index, image_generator as ig

# 1. no folder -> generation
assert pa.next_image("Fluffy roommate") is None
print("1. 沒有素材資料夾時回 None,走生成 ✅")

# 2. supplied images win, least-used first
folder = pa.product_dir("Fluffy roommate"); folder.mkdir(parents=True)
for n in ("b-second.png", "a-first.png"):
    (folder / n).write_bytes(b"\x89PNG fake")
(folder / "notes.txt").write_text("ignored")
picks=[]
for _ in range(4):
    data, path = pa.next_image("Fluffy roommate")
    picks.append(path.name)
print("2. 連續四次取用:", picks)
assert picks == ["a-first.png","b-second.png","a-first.png","b-second.png"], picks
print("   least-used 輪流,不會同一張一直出現;.txt 被忽略 ✅")

# 3. category reaches the prompt, and is omitted cleanly when absent
assert ig._category_line("a domestic cat").strip().startswith("What it actually is")
assert ig._category_line("") == ""
print("3. category 有值才出現在 prompt,沒有就整行省略 ✅")

# 4. the two new rules are in the instruction
t = ig._PROMPT_INSTRUCTION
assert "The subject must be visible and recognisable as what it actually is" in t
assert "FLUX does not know the name and will invent something" in t
assert "do not assume it is a product" in t
assert "the scene must obey physics" in t and "cartoon, illustrated or fantasy" in t
print("4. 產品必須可辨識 + 非卡通就要符合物理,兩條規則都在 ✅")

# 5. category stored on the index
topic_index.save_index("Fluffy roommate", {"product":"Fluffy roommate","source_hash":"x",
                                            "category":"a domestic cat","topics":[]})
assert topic_index.load_category("Fluffy roommate") == "a domestic cat"
assert topic_index.load_category("Nonexistent") == ""
print("5. category 存取正常,沒有索引時回空字串 ✅")
print("\nall passed")
