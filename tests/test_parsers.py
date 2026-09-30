"""Every parser, against the exact strings that broke it.

Each case below is a verbatim reply from a real run that was silently
discarded, not an invented one. See
.claude/skills/parse-what-models-return.
"""
import os, shutil, sys
from pathlib import Path
R = Path(sys.argv[1]); shutil.rmtree(R, ignore_errors=True); R.mkdir(parents=True)
os.environ["CONTENTMASTER_DATA_ROOT"] = str(R)

from contentmaster.draft_generator import _parse_blocks, _parse_axis
from contentmaster.video_generator import parse_script
from contentmaster.topic_index import parse_topics_response, clean_category

# --- drafting: every line prefixed "-- ", produced 0 drafts -------------
REAL_DRAFT = "\n".join([
    "-- POST: Understanding cat behaviour starts with watching, not correcting.",
    "-- TOPIC: Self-Care Behaviors", "-- WHY: it is the least used topic",
    "-- IS: statement, no hashtags", "-- TESTING: opening = statement",
    "-- EVIDENCE-USED: none", "-- EVIDENCE-AGAINST: none"])
assert len(_parse_blocks(REAL_DRAFT)) == 1
for prefix in ("", "* ", "1. ", "> ", "- ", "-- ", "  "):
    assert len(_parse_blocks(f"{prefix}POST: a\nTOPIC: b")) == 1, prefix
print("1. 草稿:行首的 --、項目符號、編號都容忍 ✅")

# --- video: "-- " written BEFORE each block, produced 0 beats ----------
REAL_SCRIPT = "-- \nSAY: A\nSHOW: B\n\n-- \nSAY: C\nSHOW: D"
assert len(parse_script(REAL_SCRIPT)) == 2
assert len(parse_script("SAY: A\nSHOW: B\n---\nSAY: C\nSHOW: D")) == 2
assert len(parse_script("SAY: A\nSHOW: B\nSAY: C\nSHOW: D")) == 2
assert len(parse_script("SAY: A\nSHOW: B\n---\nSAY: C\n---")) == 1
print("2. 影片:分隔線在前、在後、完全沒有,都解析得出 ✅")

# --- topics: numbered list, cost the whole expanded whitepaper ---------
REAL_TOPICS = """This text is a comprehensive guide to caring for cats.

The text is organized into several sections, including:

1. **Boredom in Cats**: This section addresses the issue of boredom and how to stimulate them.
2. **Spraying in Cats**: This section provides guidance on addressing spraying, a sign of stress.
3. **Attacking People or Other Pets**: This section offers advice on preventing aggression."""
got = [t["topic"] for t in parse_topics_response(REAL_TOPICS)]
assert got == ["Boredom in Cats", "Spraying in Cats", "Attacking People or Other Pets"], got
assert not any(t.lower().startswith(("the text", "this text")) for t in got)
print("3. 主題:編號清單解析得出,回答本身的鋪陳句不算主題 ✅")

exact = "Topic: Risks | Brief: Outdoor access raises risk.\nTopic: Needs | Brief: Cats need interaction."
assert [t["topic"] for t in parse_topics_response(exact)] == ["Risks", "Needs"]
print("4. 主題:要求的格式仍然優先,不會被重新詮釋 ✅")

# --- topics: pure prose, the sixth shape in one week -------------------
from contentmaster.topic_index import reformat_topics
PROSE = ("This text is a comprehensive guide to understanding and caring for cats. It "
         "discusses the importance of keeping cats indoors, and covers behavioural issues "
         "including spraying and scratching.\n\nSome key takeaways:\n"
         "* Indoor cats may be safer but can experience boredom.\n"
         "* Regular veterinary check-ups are essential.")
# The reformat step is a model call; stubbed so this suite stays offline.
stub = lambda prompt: ("Topic: Indoor Living | Brief: Indoor cats are safer but can be bored.\n"
                       "Topic: Veterinary Care | Brief: Regular check-ups are essential.")
got = [t["topic"] for t in reformat_topics(PROSE, complete_fn=stub)]
assert got == ["Indoor Living", "Veterinary Care"], got
print("5. 主題:純散文交給本地模型改寫,而不是丟掉 ✅")

assert reformat_topics("", complete_fn=stub) == []
assert reformat_topics(PROSE, complete_fn=lambda p: None) == []
print("6. 改寫失敗時回空,不會假裝有結果 ✅")

# --- category: a summary is not a noun phrase --------------------------
assert clean_category("a domestic cat") == "a domestic cat"
assert clean_category("This is a large document that appears to be a comprehensive guide to "
                      "understanding and managing various behaviors in cats.") == ""
assert clean_category("a cat\n1. Scratching") == ""
print("7. category:長篇摘要被擋下,不會污染後續每個 prompt ✅")

# --- test axis: the model returned the placeholders themselves ---------
assert _parse_axis("axis = tone, side: negative") == ("tone", "negative")
assert _parse_axis("opening = question") == ("opening", "question")
assert _parse_axis("axis: tone, arm: playful") == ("tone", "playful")
print("8. 測試軸:模型照抄佔位符時仍解析得出真正的軸 ✅")
print("\nall passed")
