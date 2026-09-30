"""The real document's real output, byte for byte, plus invariants that hold
for ANY document.

Why this file exists (2026-09-30). Every other extraction test in tests/ is
written from a fixture I composed by hand, and a fixture composed by hand
encodes the same beliefs as the code it tests — so it passes whenever the code
is self-consistent, not when the output is right. Four bugs shipped green that
way in one week:

  - a group heading swallowed the next five sections ("Covers: Step 1…, Step 2…,
    4. Advanced Applications, Emotional Support, …")
  - italics lost only their opening star ("the species Felis catus*.")
  - a section's seven bullet items were dropped because its colon lead-in sat
    one line below its summary
  - briefs ran from 28 to 869 characters, unbounded

All four happened while the count was already correct, and the only real-document
assertion was `len(real) == 41`. Counting is not reading.

So: the golden file below IS the current output, captured from the document
rather than reconstructed. Any change to it shows up as a diff a person has to
look at and approve, which is the step that was missing. Regenerate it
deliberately, never to make this test pass:

    ./.venv/bin/python scripts/update_golden.py
"""
import difflib
import os
import shutil
import sys
from pathlib import Path

R = Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"] = str(R)

from contentmaster.document import (  # noqa: E402
    MAX_BRIEF_CHARS, headings_and_skipped_for, headings_for, images_for,
    pandoc_available,
)

DOC = Path("Demo-white paper/cat.rtfd")
GOLDEN = Path("tests/golden/cat-whitepaper.txt")


def render(doc: Path) -> str:
    lines = []
    headings = headings_for(doc)
    lines.append(f"TOPICS {len(headings)}")
    for i, h in enumerate(headings, start=1):
        lines.append(f"{i:3}. {h['topic']}")
        lines.append(f"     {h['brief']}")
    images = images_for(doc)
    lines.append(f"IMAGES {len(images)}")
    for x in images:
        lines.append(f"     {x['file']} -> {x['section']}")
    return "\n".join(lines) + "\n"


if not pandoc_available() or not DOC.exists():
    print("跳過 — pandoc 或範例文件不在")
    print("\nall passed")
    raise SystemExit(0)

# --- 1. 逐字比對真實輸出 ---
actual = render(DOC)
expected = GOLDEN.read_text()
if actual != expected:
    diff = list(difflib.unified_diff(
        expected.splitlines(), actual.splitlines(),
        fromfile="golden (approved)", tofile="actual (now)", lineterm="", n=1))
    print("\n".join(diff[:60]))
    raise AssertionError(
        f"抽取結果和核准過的 golden 檔不同（差異 {len(diff)} 行,上面最多顯示 60 行）。"
        "讀過差異、確認每一處都是你想要的之後,再跑 scripts/update_golden.py。"
    )
print("1. 真實文件的輸出跟核准過的 golden 檔逐字相同 ✅")

# --- 2. 對任何文件都該成立的不變條件 ---
headings, skipped = headings_and_skipped_for(DOC)

leaks = [(h["topic"], h["brief"]) for h in headings
         if any(m in h["brief"] or m in h["topic"]
                for m in ("**", "](", "\\", "^", "¬", "  "))]
assert not leaks, f"摘要或標題裡殘留 markdown/排版符號: {leaks[:3]}"
print("2. 41 個主題裡沒有一個殘留 markdown 或排版符號 ✅")

toolong = [(len(h["brief"]), h["topic"]) for h in headings
           if len(h["brief"]) > MAX_BRIEF_CHARS]
assert not toolong, f"摘要超過 {MAX_BRIEF_CHARS} 字元: {toolong}"
print(f"3. 每個摘要都在 {MAX_BRIEF_CHARS} 字元以內 ✅")

empty = [h["topic"] for h in headings if not h["brief"].strip() or not h["topic"].strip()]
assert not empty, f"標題或摘要是空的: {empty}"
print("4. 沒有空標題、沒有空摘要 ✅")

# 一個章節的摘要若等於另一個章節的摘要,表示它偷了鄰居的內文
briefs = {}
stolen = []
for h in headings:
    key = h["brief"][:80]
    if key in briefs and not h["brief"].startswith("Covers:"):
        stolen.append((briefs[key], h["topic"]))
    briefs[key] = h["topic"]
assert not stolen, f"兩個章節共用同一段內文（其中一個偷了鄰居的）: {stolen}"
print("5. 沒有兩個章節共用同一段內文 ✅")

# "Covers:" 只能列出真正在它底下的東西 —— 不能跨過下一個編號章節
import re  # noqa: E402
numbered = re.compile(r"^\d+(?:\.\d+)*[.)]?\s")
order = [h["topic"] for h in headings]
for h in headings:
    if not h["brief"].startswith("Covers: "):
        continue
    kids = [k.strip() for k in h["brief"][len("Covers: "):].rstrip(", …").split(", ")]
    start = order.index(h["topic"])
    for k in kids:
        if k not in order:
            continue  # truncated by the length cap
        between = order[start + 1:order.index(k)]
        assert not any(numbered.match(b) for b in between), (
            f"{h['topic']!r} 的 Covers 跨過了編號章節,收到 {k!r}：中間有 {between}")
print("6. 每個 Covers 都停在下一個編號章節之前 ✅")

dupes = [t for t in order if order.count(t) > 1]
assert not dupes, f"重複的標題: {set(dupes)}"
print("7. 沒有重複標題 ✅")

# 被跳過的行不能同時出現在採用清單裡
overlap = {r["topic"] for r in skipped} & set(order)
assert not overlap, f"同一行同時被採用又被列為跳過: {overlap}"
assert all(r.get("reason") for r in skipped), "被跳過的行必須說明原因"
print(f"8. {len(skipped)} 行待確認,每行都有原因,且沒有跟採用清單重複 ✅")

# 每張內嵌圖片都要對到一個真實存在的章節
for x in images_for(DOC):
    assert x["section"] in order, f"{x['file']} 對到的章節不在主題清單裡: {x['section']!r}"
print("9. 每張圖片對到的章節都在主題清單裡 ✅")

print("\nall passed")
