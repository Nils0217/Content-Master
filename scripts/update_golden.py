"""Rewrite tests/golden/cat-whitepaper.txt from the current extractor.

Run this ONLY after reading the diff that test_golden_extraction.py printed and
deciding every change in it is wanted. Running it to make the test green is the
one thing it must not be used for — that converts the check back into the thing
it replaced.

    ./.venv/bin/python scripts/update_golden.py
"""
import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from contentmaster.document import headings_for, images_for, pandoc_available  # noqa: E402

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


if not pandoc_available():
    raise SystemExit("pandoc 不在,產出會退到純文字路徑 — 不要用它覆蓋 golden 檔。")
if not DOC.exists():
    raise SystemExit(f"找不到 {DOC}")

new = render(DOC)
old = GOLDEN.read_text() if GOLDEN.exists() else ""
if new == old:
    print("沒有變化。")
    raise SystemExit(0)

print("\n".join(difflib.unified_diff(
    old.splitlines(), new.splitlines(),
    fromfile="golden (現在核准的)", tofile="golden (要寫入的)", lineterm="", n=1)))
print()
answer = input("以上差異全部都是你想要的嗎？寫入 golden 檔 [y/N] > ").strip().lower()
if answer != "y":
    raise SystemExit("沒有寫入。")
GOLDEN.parent.mkdir(parents=True, exist_ok=True)
GOLDEN.write_text(new)
print(f"已寫入 {GOLDEN}")
