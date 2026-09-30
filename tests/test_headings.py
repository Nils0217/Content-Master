"""Section headings read from the document itself, no retrieval service."""
import os, shutil, sys
from pathlib import Path
R = Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"] = str(R)

from contentmaster.document import extract_headings
from contentmaster.pipeline import COGNEE_ENABLED

assert COGNEE_ENABLED is False
print("1. Cognee 預設不參與主題抽取 ✅")

DOC = """How to Use a Cat
A Practical Guide

Abstract
Cats are widely regarded as independent and highly adaptable companions in many homes.

1. Introduction
A cat is a domesticated mammal belonging to the species Felis catus, unlike many devices.

2. Required Inputs
Before attempting to interact with a cat, prepare clean water, food and a litter box.
• Clean water
• Appropriate cat food

3. Basic Operating Procedure
Allow the cat to approach voluntarily rather than picking it up or chasing it around.
"""
h = extract_headings(DOC)
assert [x["topic"] for x in h] == ["Introduction", "Required Inputs", "Basic Operating Procedure"], h
print("2. 抓到編號章節標題,順序正確 ✅")

assert all(len(x["brief"]) >= 40 for x in h)
assert "•" not in h[1]["brief"], "條列符號不該被當成摘要"
print("3. 每個章節配到第一句實質內容,不是條列項 ✅")

assert not any(x["topic"] in ("Abstract", "How to Use a Cat", "A Practical Guide") for x in h)
print("4. 沒有編號的標題和文件標題不會混進來 ✅")

assert extract_headings("") == []
assert extract_headings("Just one long paragraph with no headings at all in it anywhere.") == []
print("5. 沒有章節時回空清單,不亂猜 ✅")

dup = DOC + "\n1. Introduction\nA repeated heading should not appear twice in the list at all.\n"
assert [x["topic"] for x in extract_headings(dup)].count("Introduction") == 1
print("6. 重複標題去重 ✅")
# --- pandoc 路徑:位置優先,粗體只是其中一個訊號(2026-09-28)---
# 這份白皮書用了三種排版慣例,而且作者兩次都漏標系列裡的第一個
# （Step 1 沒粗體 / Step 2-4 有;Problem 1 沒粗體 / Problem 2-4 有）。
# 所以標題的定義是「形狀 + 位置」,粗體只是三個「獨立成行」訊號之一。
from contentmaster.document import extract_doc_headings, headings_for, images_for, pandoc_available

MD = """**How to Use a Cat**

A Practical Guide to Human–Cat Interaction

Abstract

Cats are widely regarded as independent and highly adaptable companions in homes.

**1. Introduction**

A cat is a domesticated mammal belonging to the species *Felis catus*, unlike devices.

**Do not control the cat. Create conditions in which the cat chooses to participate.**

**3. Basic Operating Procedure**

Step 1: Establish Trust

Allow the cat to approach voluntarily rather than picking it up or chasing it about.

**Step 2: Identify the Cat’s Preferences**

Some cats enjoy being touched on the head or cheeks. Others prefer no contact.

Every cat has different preferences.

**Generally positive signals:**

- Relaxed posture

**6. Troubleshooting**

Problem: The cat ignores you.
Solution: This may be normal. Continue providing food, safety and respectful care.
**Problem: The cat wakes you at night.**
Solution: Evaluate feeding schedules, play routines and potential medical issues.

**Comparing lifestyle risks**

  -------------------------------------
  Increased risks associated with living strictly indoors all year round

**Physical Behaviors in Cats**\\
Napping

Although domestic cats no longer need to hunt, their genetic makeup still rules.

Cat-Eating-Grass.jpg.webp ¬

**In practical terms the sequence is:**

**Feed → Clean → Play → Respect → Observe → Repeat.**

**Final recommendation:**

Do not attempt to control the cat. Build a good environment and earn its trust.
"""
h = extract_doc_headings(MD)
topics = [x["topic"] for x in h]

assert "Step 1: Establish Trust" in topics, topics
print("7. 沒粗體的標題抓得到（Step 1 —— 作者漏標的那個）✅")

assert "Napping" in topics and "Physical Behaviors in Cats" in topics, topics
print("8. 貼進來那段的純文字標題抓得到 ✅")

assert "Problem: The cat ignores you." in topics, topics
assert "Problem: The cat wakes you at night." in topics, topics
print("9. Label: 句子 形式的條目留著,不管有沒有粗體 ✅")

assert "Every cat has different preferences." not in topics, topics
print("10. 短的完整句子不是標題（結尾句點、又沒有冒號）✅")

assert not any(t.startswith("Do not control the cat") for t in topics), topics
print("11. 粗體標語不是標題（14 字超過上限）✅")

assert "Generally positive signals:" not in topics, topics
assert "Final recommendation:" not in topics, topics
print("12. 冒號結尾的引導語不是標題 ✅")

assert not any("Feed" in t and "Repeat" in t for t in topics), topics
print("13. 引導語後面那行是它的內容,不是標題 ✅")

assert "How to Use a Cat" not in topics, topics
assert "A Practical Guide to Human–Cat Interaction" not in topics, topics
print("14. 文件標題區塊（標題+副標,都沒有自己的內文）跳過 ✅")

assert "Abstract" not in topics, topics
print("15. Abstract 這種文件結構標籤不算主題 ✅")

risks = [x for x in h if x["topic"] == "Comparing lifestyle risks"]
assert risks and risks[0]["brief"].startswith("Increased risks"), risks
print("16. 內文只存在於表格裡的章節,摘要退到表格內文 ✅")

proc = [x for x in h if x["topic"] == "3. Basic Operating Procedure"]
assert proc and proc[0]["brief"].startswith("Covers:"), proc
assert "Step 1: Establish Trust" in proc[0]["brief"], proc
print("17. 沒有自己內文的章節列出底下有什麼,不偷下一節的內文 ✅")

assert extract_doc_headings("") == []
print("18. 空輸入回空清單 ✅")

# --- 你貼回來的實跑輸出暴露的四個錯（2026-09-29）---
GROUPS = """**2. Required Inputs**

Before you begin, prepare water, food, a litter box and a reasonable amount of patience.

**3. Basic Operating Procedure**

Step 1: Establish Trust

Allow the cat to approach voluntarily rather than picking it up or chasing it.

**4. Advanced Applications**

Once basic trust is established, cats can be integrated into daily activities.

**Emotional Support**

A calm cat sitting nearby can provide companionship and a sense of routine here.
"""
g = extract_doc_headings(GROUPS)
proc = [x for x in g if x["topic"] == "3. Basic Operating Procedure"][0]
assert "Step 1: Establish Trust" in proc["brief"], proc
assert "4. Advanced Applications" not in proc["brief"], proc
assert "Emotional Support" not in proc["brief"], proc
print("21. group 的範圍停在下一個編號章節,不吞後面整份文件 ✅")

ITALIC = """**1. Introduction**

A cat is a domesticated mammal of the species *Felis catus*. It has no manual.
"""
intro = extract_doc_headings(ITALIC)[0]
assert "Felis catus." in intro["brief"], intro
assert "*" not in intro["brief"], intro
print("22. 斜體的前後星號都拿掉（原本留下 'Felis catus*'）✅")

TABLE = """**Comparing lifestyle risks**

  -----------------------------------------
  Increased risks indoors                   Increased risks outdoors
"""
tb = extract_doc_headings(TABLE, min_brief=20)[0]
assert "  " not in tb["brief"], repr(tb["brief"])
print("23. 表格的欄位間距收成單一空白 ✅")

LIST = """**2. Required Inputs**

Before attempting to interact with a cat, prepare the following:

- Clean water

- Appropriate cat food

- Patience

**3. Next Section**

This section has prose of its own so it does not become a group at all here.
"""
li = [x for x in extract_doc_headings(LIST) if x["topic"] == "2. Required Inputs"][0]
for item in ("Clean water", "Appropriate cat food", "Patience"):
    assert item in li["brief"], li
assert "- " not in li["brief"], li
print("24. 冒號結尾的摘要會把清單接進來（pandoc 的清單中間有空行）✅")

# --- 文件自己宣告 heading 時,一條啟發式都不跑（2026-09-29）---
# 這是「別人的文件要不要改程式碼」的答案:Word / Google Docs 的 heading
# style、Markdown 原始檔、HTML h1-h6、有 tag 的 PDF,到 pandoc 都是真的
# heading,輸出成 # / ##。這種文件以前抓到 0 個 —— `#` 在跑啟發式之前
# 就被當成雜訊丟掉了。
from contentmaster.document import extract_atx_headings

DECLARED = """# Introduction

Our platform reduces onboarding time for enterprise teams by a measurable margin.

## Pricing Tiers

Three tiers are available, each with a different support commitment attached.

## Security Posture

We hold SOC 2 Type II and encrypt all data at rest using customer-managed keys.

# Roadmap

Two major releases are planned for the coming year, both driven by customer asks.
"""
d = extract_doc_headings(DECLARED)
assert [x["topic"] for x in d] == ["Introduction", "Pricing Tiers",
                                   "Security Posture", "Roadmap"], d
print("25. 宣告 heading 的文件直接讀它的 heading（原本 0 個）✅")

# 每個字都很長、又沒有粗體、又不編號 —— 啟發式會全滅,宣告路徑不在乎
LONG = """## A Heading That Is Deliberately Far Longer Than Seventy Characters And Many Words

The body text of this section is long enough to serve as a brief for the heading.
"""
assert [x["topic"] for x in extract_atx_headings(LONG)][0].startswith("A Heading That Is"), LONG
print("26. 宣告路徑不套長度和字數上限（那些是為某一份文件調出來的）✅")

NESTED = """# Platform Overview

## Pricing Tiers

Three tiers are available, each with a different support commitment attached.

## Security Posture

We hold SOC 2 Type II and encrypt all data at rest using customer-managed keys.

# Roadmap

Two major releases are planned for the coming year, both driven by customer asks.
"""
n = extract_doc_headings(NESTED)
top = [x for x in n if x["topic"] == "Platform Overview"][0]
assert top["brief"] == "Covers: Pricing Tiers, Security Posture", top
print("27. 父章節的範圍由真正的層級決定,不會吞到下一個同級章節 ✅")

# --- 真實文件 ---
DOC_PATH = Path("Demo-white paper/cat.rtfd")
if pandoc_available() and DOC_PATH.exists():
    real = headings_for(DOC_PATH)
    assert len(real) == 41, f"手數是 41 個主題,得到 {len(real)}"
    assert all(x["brief"] and x["topic"] for x in real)
    assert not any("](" in x["brief"] for x in real), "摘要裡不該留 markdown 連結"
    print(f"19. 真實文件抓到 {len(real)} 個章節（純文字路徑只有 7 個）✅")

    imgs = images_for(DOC_PATH)
    pairs = {i["file"]: i["section"] for i in imgs}
    assert pairs.get("Cat-Eating-Grass.jpg.webp") == "Eating Grass", pairs
    assert pairs.get("Cat-Grooming.jpg.webp") == "Grooming and Licking", pairs
    assert pairs.get("Cat-Ear-Movement.jpg.webp") == "Ear and Tail Movement", pairs
    print(f"20. {len(imgs)} 張內嵌圖片各自對應到它所屬的章節 ✅")
else:
    print("19-20. 跳過 — pandoc 或範例文件不在")

# --- 被跳過的行要讓人撿回來（2026-09-29）---
# 啟發式的門檻全是為某一份文件調出來的,所以在別人的文件上一定會拒絕
# 真的章節。以前拒絕是靜默的 —— 審核的人根本不知道有東西不見了。
import io
from contentmaster.document import extract_doc_headings_with_skipped, headings_and_skipped_for
from contentmaster import topic_index as _ti

# 有人用「全大寫 + 冒號」標章節。我們的規則必定拒絕,但人看得出來
OTHER = """Overview

This product helps small teams publish consistently without hiring an agency.

PRICING:

Three tiers, each with a different support commitment and a different SLA.

SECURITY:

We hold SOC 2 Type II and encrypt everything at rest with customer-managed keys.
"""
found, skipped = extract_doc_headings_with_skipped(OTHER)
assert [x["topic"] for x in found] == ["Overview"], found
assert [r["topic"] for r in skipped] == ["PRICING:", "SECURITY:"], skipped
print("28. 用別的慣例排版時,沒認出的章節會被列出來而不是靜默丟掉 ✅")

assert all(r.get("reason") for r in skipped), skipped
assert all(r.get("brief") for r in skipped), skipped
print("29. 每個被跳過的都附上原因和現成的摘要 ✅")

import sys
_stdin = sys.stdin
sys.stdin = io.StringIO("1 2\n")
try:
    picked = _ti._review_skipped(skipped)
finally:
    sys.stdin = _stdin
assert [x["topic"] for x in picked] == ["PRICING:", "SECURITY:"], picked
assert all(x["brief"] for x in picked), picked
print("30. 人輸入編號就能把它們撿回成真的主題 ✅")

# 單行段落不該混進清單 —— 它長又以句點結尾,是句子兩次
assert not any(r["topic"].startswith("This product helps") for r in skipped), skipped
assert not any(r["topic"].startswith("We hold SOC 2") for r in skipped), skipped
print("31. 單行段落不會塞進待撿清單（長又以句點結尾）✅")

# 文件自己宣告 heading 時,沒有東西需要被二次猜測
_declared, none_skipped = extract_doc_headings_with_skipped(DECLARED)
assert none_skipped == [], none_skipped
print("32. 宣告 heading 的文件跳過清單是空的（沒有猜測就沒有誤殺）✅")

if pandoc_available() and DOC_PATH.exists():
    _h, sk = headings_and_skipped_for(DOC_PATH)
    assert len(_h) == 41, len(_h)
    assert 5 <= len(sk) <= 25, f"{len(sk)} 行待撿 —— 太多就沒人看,太少就撿不回東西"
    assert all(r["topic"] and r["reason"] for r in sk)
    print(f"33. 真實文件:41 個主題 + {len(sk)} 行列給人確認 ✅")

# --- 你貼回來的第二輪輸出暴露的兩個問題（2026-09-30）---
from contentmaster.document import MAX_BRIEF_CHARS

LATE_LEAD = """**5. Maintenance Requirements**

Cats require ongoing care rather than occasional operation.
Minimum responsibilities include:

- Regular feeding and access to fresh water

- Clean litter facilities

- Monitoring for behavioral or health changes

**6. Next Section**

This one has a paragraph of its own so it never becomes a group in this test.
"""
m = [x for x in extract_doc_headings(LATE_LEAD)
     if x["topic"] == "5. Maintenance Requirements"][0]
for item in ("Regular feeding", "Clean litter facilities", "Monitoring for behavioral"):
    assert item in m["brief"], m
print("34. 冒號引導語在摘要的下一行時,清單照樣接得到 ✅")

LONG = "**A Section**\n\n" + (
    "This sentence is here purely to make the paragraph long. " * 20)
long_brief = extract_doc_headings(LONG)[0]["brief"]
assert len(long_brief) <= MAX_BRIEF_CHARS, len(long_brief)
assert long_brief.rstrip().endswith("."), repr(long_brief[-40:])
print(f"35. 摘要上限 {MAX_BRIEF_CHARS} 字元,而且切在句子邊界 ✅")

if pandoc_available() and DOC_PATH.exists():
    real2 = headings_for(DOC_PATH)
    assert all(len(x["brief"]) <= MAX_BRIEF_CHARS for x in real2), \
        max((len(x["brief"]), x["topic"]) for x in real2)
    maint = [x for x in real2 if x["topic"] == "5. Maintenance Requirements"][0]
    assert "Monitoring for behavioral or health changes" in maint["brief"], maint
    print("36. 真實文件:每個摘要都在上限內,第 12 節的七個條目都在 ✅")

print("\nall passed")
