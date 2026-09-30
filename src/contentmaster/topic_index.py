"""Topic index — replaces the old "one fixed Cognee query -> one context
blob" extraction (2026-09-15, docs/SCHEDULE.md Phase 9 / docs/LOG.md for
the full /grill-me design session).

The problem this closes: `step1_cognee_extract()` used to ask Cognee the
exact same question ("product name, key features, target ICP") on every
single run, re-paying full extraction cost each time, and handing
draft_generator the same blob of text regardless of --product-name or how
many times the pipeline had already run — which is *why* repeat runs kept
writing near-identical posts (same input -> similar output from a small
local model), not a bug in the LLM call itself.

Now: Cognee is asked to list the document's distinct topics (not answer
one fixed question), each as {topic, brief}. That list is cached locally,
keyed by a hash of the whitepaper file — unchanged file means *zero*
Cognee calls on the next run, real savings, not just fewer tokens. A human
reviews only genuinely *new* topics (existing ones, and their usage
counts, are never re-shown). draft_generator then picks the least-used
topics so variety comes from real, distinct extracted facts, not a
hardcoded "draft 1 covers X" prompt template that wouldn't scale to a
different document's structure.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from .config import settings
from .human_loop import ReviewInterrupted
from .slug import slugify

PLAYS_DIR = settings.data_root / "plays"
# 2026-09-17 (code scan): topic index files used to live directly in
# plays/ as {product}_topics.json — collided with warehouse/models/
# staging/stg_plays.sql's `plays/*.json` glob (different schema, DuckDB's
# union_by_name errored trying to combine them). Own subdirectory so this
# can't happen again even if something else drops a .json in plays/ later.
TOPICS_DIR = PLAYS_DIR / "topics"


def _index_path(product_name: str) -> Path:
    return TOPICS_DIR / f"{slugify(product_name, default='product')}.json"


def file_hash(path: str | Path) -> str:
    """Cheap, purely local — reads the file's bytes and hashes them.
    No network call, no Cognee, no LLM involved; this is what lets
    sync_topics() decide "nothing changed" without ever touching Cognee.

    Resolves an .rtfd bundle to the .rtf inside it, so hashing the text is
    what decides whether to re-extract. Hashing the directory is not
    possible, and hashing the bundle's images would make re-extraction
    fire every time a picture was swapped without the words changing.
    """
    from .document import resolve_bundle

    return hashlib.sha256(resolve_bundle(Path(path)).read_bytes()).hexdigest()


def load_index(product_name: str) -> dict[str, Any] | None:
    path = _index_path(product_name)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_index(product_name: str, index: dict[str, Any]) -> None:
    TOPICS_DIR.mkdir(parents=True, exist_ok=True)
    _index_path(product_name).write_text(json.dumps(index, indent=2, default=str))


# The format actually asked for.
_TOPIC_BRIEF = re.compile(r"topic\s*:\s*(.+?)\s*\|\s*brief\s*:\s*(.+)$", re.IGNORECASE)

# The format models actually return. A numbered or bulleted list with the
# name in bold and the summary after a colon:
#
#     1. **Spraying in Cats**: This section provides guidance on how to...
#     - Boredom in Cats: This section addresses the issue of boredom...
#
# 2026-09-26: this cost a whole document. The whitepaper was expanded from
# 12k to 22k characters, Cognee read all of it, and its answer named every
# new section — Boredom, Spraying, Attacking People or Other Pets,
# Urinating Outside the Litter Box. Not one line matched
# "Topic: X | Brief: Y", so the parser returned nothing, "no new topics"
# and "the extraction was unparseable" were indistinguishable, and the
# index kept its original four topics while recording the new document's
# hash as processed. The new material was unreachable and nothing said so.
_NUMBERED = re.compile(
    r"^[\s\-*>]*(?:\d+[.)]\s*)?\**\s*(?P<topic>[^:*]{3,60}?)\s*\**\s*:\s+(?P<brief>.{20,})$")

# Lines that are the answer's own scaffolding, not a topic.
_NOT_A_TOPIC = re.compile(
    r"^(the (text|document)|this (text|document)|it|here|sections?|summary|overview|"
    r"in (summary|conclusion)|organi[sz]ed|includ)", re.IGNORECASE)


_REFORMAT_INSTRUCTION = """Below is someone's description of a document. Turn it into a list of the distinct topics it describes.

One topic per line, in exactly this format and nothing else:
Topic: <short name, 1-4 words> | Brief: <one sentence, taken from the description>

Rules:
- Only topics the description actually mentions. Invent nothing.
- Skip sentences about the document itself ("this text is a guide", "it covers various areas").
- A topic is a subject someone could write a post about, not a summary of the whole thing.

Description:
{text}"""


def reformat_topics(text: str, complete_fn: Callable[[str], str | None] | None = None
                    ) -> list[dict[str, str]]:
    """Have a local model restate a prose answer as topic lines.

    2026-09-26. The extractor is asked for `Topic: X | Brief: Y` and has
    now answered in six different shapes across one week — bold numbered
    headings, plain numbered sentences, bulleted takeaways, and this time
    three paragraphs of prose with no per-topic structure at all. Each one
    carried the real content; each one parsed to nothing.

    Widening the regex again is the losing move. It treats the format as
    the thing to chase, when the format is the one part nothing controls:
    Cognee's GRAPH_COMPLETION exists to *write a narrative answer*, so
    asking it to emit fields is asking it not to do its job.

    Reformatting is a different task, and a small local model is reliable
    at it — the content is handed over rather than recalled, so there is
    nothing to get wrong except the shape. Used only when direct parsing
    finds nothing, so a well-formed answer never takes the detour.
    """
    if not text.strip():
        return []
    if complete_fn is None:
        complete_fn = _local_complete
    answer = complete_fn(_REFORMAT_INSTRUCTION.format(text=text.strip()[:6000]))
    restated = parse_topics_response(answer or "", reformat=False)
    return _grounded_only(restated, text)


# Words too common to prove anything about where a topic name came from.
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in into is it its of on or that the "
    "their there these this to was were what when where which who will with your".split()
)

# How much of a word has to match. Prefix rather than exact so an
# inflection ("spraying" against "spray") still counts as the same word.
_STEM_CHARS = 5


def _grounded_only(topics: list[dict[str, str]], source: str) -> list[dict[str, str]]:
    """Drop a restated topic whose name appears nowhere in the source.

    2026-09-28. Handing prose to a model and asking for topic lines gets a
    model that would rather answer than return nothing. Given "Some prose
    that contains no topics at all whatsoever." it produced two topics —
    "Empty Text — A document containing no topics or subjects." and
    "Rules — The document outlines rules for a specific purpose." Neither
    word is in the input. They were then stored, the document was marked
    processed, and every later prompt for that product carried two topics
    that do not exist in the whitepaper.

    Reformatting is supposed to change the SHAPE of an answer, never its
    content, so a name with no word in common with the source did not come
    from the source. Checked on stems rather than whole words, and passed on
    one match rather than all, because dropping real material is the worse
    of the two mistakes.
    """
    haystack = source.lower()
    kept, dropped = [], []
    for t in topics:
        words = [w for w in re.findall(r"[^\W_]+", t["topic"].lower())
                 if len(w) >= 4 and w not in _STOPWORDS]
        if not words or any(w[:_STEM_CHARS] in haystack for w in words):
            kept.append(t)
        else:
            dropped.append(t["topic"])
    if dropped:
        print(f"[warn] Dropped {len(dropped)} restated topic(s) whose name appears nowhere in "
              f"the extracted text, so they were invented rather than reformatted: "
              f"{', '.join(repr(d) for d in dropped)}")
    return kept


def _local_complete(prompt: str) -> str | None:
    import os

    import requests

    endpoint = os.environ.get("LLM_IMPROVE_ENDPOINT", "http://localhost:11434/v1/chat/completions")
    model = os.environ.get("LLM_IMPROVE_MODEL", "llama3.2:3b")
    try:
        resp = requests.post(
            endpoint,
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:  # noqa: BLE001 — a reformat failing is reported by the caller
        return None


def parse_topics_response(text: str, reformat: bool = True) -> list[dict[str, str]]:
    """Cognee's free-text answer into structured topics.

    Two shapes are accepted: the "Topic: X | Brief: Y" the query asks for,
    and the numbered/bulleted list a model produces when it answers in its
    own words. Preferring the first means a well-formed answer is never
    reinterpreted; accepting the second means a well-informed answer is
    not thrown away over punctuation.

    Still deliberately loose about what counts as a topic — the human
    review step is what catches a garbled extraction, not this parser. Its
    job is to not lose material that is plainly there.
    """
    topics: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(topic: str, brief: str) -> None:
        topic, brief = topic.strip().strip("*").strip(), brief.strip()
        key = topic.lower()
        if not topic or not brief or key in seen or _NOT_A_TOPIC.match(topic):
            return
        seen.add(key)
        topics.append({"topic": topic, "brief": brief})

    lines = text.splitlines()
    for line in lines:
        m = _TOPIC_BRIEF.match(line.strip("-* \t"))
        if m:
            add(m.group(1), m.group(2))
    if topics:
        return topics

    for line in lines:
        m = _NUMBERED.match(line.strip())
        if m:
            add(m.group("topic"), m.group("brief"))
    if topics or not reformat:
        return topics
    # Neither shape matched. Hand the prose to a local model and ask for
    # the shape, rather than adding a seventh pattern.
    print("[info] The extractor answered in prose; asking the local model to restate it as "
          "topics rather than discarding it.")
    return reformat_topics(text)


def _prompt_new_topic(topics: list[dict[str, str]]) -> None:
    """Appends one user-typed topic to `topics` in place. Shared by the
    top-level 't' shortcut and the in-loop 't' option so there's one
    place that defines what "type in a topic" actually asks for.
    """
    try:
        name = input("New topic name: ").strip()
        brief = input("Brief: ").strip()
    except (EOFError, KeyboardInterrupt) as e:
        raise ReviewInterrupted("Topic review interrupted (stdin closed or Ctrl+C)") from e
    if name and brief:
        topics.append({"topic": name, "brief": brief})


# How different a section's summary has to be before it is worth a human's
# attention. Set at 10% because that is roughly the line between an edit
# that changes what the section says and one that does not: a fixed typo or
# a swapped word in a 60-word brief lands under it, a rewritten first
# sentence lands over it.
BRIEF_CHANGE_THRESHOLD = 0.10


def brief_changed(old: str, new: str, threshold: float = BRIEF_CHANGE_THRESHOLD) -> bool:
    """Whether a section's summary changed enough to re-show the human.

    2026-09-28. `genuinely_new` matches on topic NAME alone, so a section
    whose name stayed the same and whose text was rewritten produced no new
    topic, no review, and no update — while `source_hash` was written as
    processed. The index kept serving the old summary into every drafting
    and image prompt, permanently, and nothing said so. That is the same
    failure the hash guard at the bottom of sync_topics() was added to stop,
    reached by a different route.

    Compared on normalised whitespace so a re-wrap (pandoc's --wrap
    setting, or an editor reflowing a paragraph) is not a change.
    """
    o = " ".join((old or "").split())
    n = " ".join((new or "").split())
    if o == n:
        return False
    if not o or not n:
        return True
    return (1.0 - difflib.SequenceMatcher(None, o, n).ratio()) > threshold


def _review_topics_interactive(new_topics: list[dict[str, str]],
                                heading: str = "NEW TOPICS EXTRACTED FROM THE WHITEPAPER",
                                ) -> list[dict[str, str]]:
    """Only called with genuinely *new* topics (ones not already in the
    index) — existing topics and their usage counts are never re-shown,
    matching "human doesn't need to spend a lot of time reviewing them".
    """
    print("\n" + "=" * 60)
    print(f"{heading} ({len(new_topics)})")
    print("-" * 60)
    for i, t in enumerate(new_topics, start=1):
        print(f" {i}. {t['topic']} — {t['brief']}")
    print("=" * 60)
    # 2026-09-15: was `if not choice.startswith("c"): return new_topics` —
    # silently treated ANY input that wasn't "c..." (including "t", which
    # a real run showed a user typing here, expecting the type-in option)
    # as "accept all as-is". Now locked to exactly a/c/t: blank no longer
    # defaults to "accept" either — an explicit choice is required every
    # time, anything else (including blank) re-prompts with an error
    # instead of silently picking a default. "t" jumps straight into
    # typing a new topic (still lands in the same edit loop afterward, so
    # further edits/deletes/type-ins are still available).
    while True:
        try:
            choice = input(
                "Accept all as-is, or make changes? [a]ccept all / [c]hange some / "
                "[t]ype in a new topic > "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt) as e:
            raise ReviewInterrupted("Topic review interrupted (stdin closed or Ctrl+C)") from e
        if choice in ("a", "c", "t"):
            break
        print(f"{choice!r} isn't a valid choice — enter exactly 'a', 'c', or 't'.")
    if choice == "a":
        return new_topics

    topics = list(new_topics)
    if choice.startswith("t"):
        _prompt_new_topic(topics)
    while True:
        print()
        for i, t in enumerate(topics, start=1):
            print(f" {i}. {t['topic']} — {t['brief']}")
        try:
            sel = input(
                "Enter topic number to [e]dit/[d]elete, [t] to type in a new topic "
                "of your own, or blank to finish > "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt) as e:
            raise ReviewInterrupted("Topic review interrupted (stdin closed or Ctrl+C)") from e
        if not sel:
            break
        if sel == "t":
            _prompt_new_topic(topics)
            continue
        if not sel.isdigit() or not (1 <= int(sel) <= len(topics)):
            print("Not a valid number, 't', or blank — try again.")
            continue
        idx = int(sel) - 1
        try:
            action = input(f"[e]dit or [d]elete #{sel} ({topics[idx]['topic']})? > ").strip().lower()
        except (EOFError, KeyboardInterrupt) as e:
            raise ReviewInterrupted("Topic review interrupted (stdin closed or Ctrl+C)") from e
        if action.startswith("d"):
            topics.pop(idx)
        elif action.startswith("e"):
            try:
                new_name = input(f"Topic name [{topics[idx]['topic']}]: ").strip() or topics[idx]["topic"]
                new_brief = input(f"Brief [{topics[idx]['brief']}]: ").strip() or topics[idx]["brief"]
            except (EOFError, KeyboardInterrupt) as e:
                raise ReviewInterrupted("Topic review interrupted (stdin closed or Ctrl+C)") from e
            topics[idx] = {"topic": new_name, "brief": new_brief}
        else:
            # 2026-09-17 (code scan): used to silently ignore anything
            # that wasn't "d"/"e" — inconsistent with every other prompt
            # in this file, which all error on invalid input.
            print(f"{action!r} isn't a valid choice — enter 'e' or 'd'.")
    return topics


def _review_skipped(skipped: list[dict[str, str]]) -> list[dict[str, str]]:
    """Show the lines the extractor turned down and let the human put any of
    them back. Returns the ones they picked.

    2026-09-29. The topic review could only ever fix what extraction FOUND. A
    section it missed was invisible: the reviewer had no way to know there was
    anything to look for, so "type in a new topic" only helped someone who had
    already read the document themselves and noticed. That is the whole reason a
    missed section — "Physical Behaviors in Cats", and the eight subsections
    under it — survived several runs unnoticed.

    Every threshold in the extractor was derived from one document, so on a
    different document it will turn down real sections. This list is what makes
    that survivable without editing code: the heuristic only has to be roughly
    right, and the human corrects it once per document (the result is cached by
    file hash, see sync_topics).
    """
    print("\n" + "-" * 60)
    print(f"LINES THE EXTRACTOR SKIPPED ({len(skipped)})")
    print("Your document may mark sections in a way it did not recognise. Anything")
    print("here that IS a real section can be added back.")
    print("-" * 60)
    for i, r in enumerate(skipped, start=1):
        print(f" {i}. {r['topic']}")
        print(f"     skipped because it {r['reason']}")
        if r.get("brief"):
            print(f"     would be summarised as: {r['brief'][:100]}")
    print("-" * 60)
    while True:
        try:
            sel = input("Numbers to add as real sections (e.g. 3 7 12), or blank for none > ").strip()
        except (EOFError, KeyboardInterrupt) as e:
            raise ReviewInterrupted("Topic review interrupted (stdin closed or Ctrl+C)") from e
        if not sel:
            return []
        parts = sel.replace(",", " ").split()
        if all(x.isdigit() and 1 <= int(x) <= len(skipped) for x in parts):
            picked, seen_n = [], set()
            for x in parts:
                n = int(x)
                if n in seen_n:
                    continue
                seen_n.add(n)
                r = skipped[n - 1]
                picked.append({"topic": r["topic"],
                               "brief": r.get("brief") or r["topic"]})
            return picked
        print(f"{sel!r} is not a list of numbers between 1 and {len(skipped)} — try again, "
              "or leave it blank to add none.")


def sync_topics(
    product_name: str, whitepaper_path: str | Path, extract_fn: Callable[[], str],
    extract_category_fn: Callable[[], str] | None = None,
    skipped_fn: Callable[[], list[dict[str, str]]] | None = None,
) -> dict[str, Any]:
    """The main entry point. Returns the up-to-date index for this
    product. Calls `extract_fn()` (real Cognee work — add/cognify/search)
    ONLY if there's no cached index yet, or the whitepaper's content
    actually changed since the index was last built. `extract_fn` should
    return Cognee's raw topic-list response text (see
    pipeline.step1_cognee_extract_topics); this module never talks to
    Cognee directly, it only parses/stores/reviews what pipeline.py hands
    it — same layering the rest of this project already uses.
    """
    index = load_index(product_name)

    # 2026-09-19 (code scan, backed by a real run in audit/events.jsonl):
    # file_hash() used to run FIRST, before the cache was even loaded, so
    # an unreadable whitepaper path raised straight out of here — and
    # pipeline.py caught it as "extraction failed", threw the topics away,
    # and fell back to the fixed-template generator. A real run lost a
    # perfectly good 4-topic cached index that way, because the path had a
    # stray trailing space (`'cat.rtf '`). The cache exists precisely so a
    # run does not depend on re-reading the source; it must not be
    # discarded because the source is momentarily unreachable.
    try:
        current_hash = file_hash(whitepaper_path)
    except OSError as e:
        if index is not None:
            print(f"[warn] Could not read {whitepaper_path} ({e}) — reusing this product's "
                  f"cached topic index ({len(index.get('topics') or [])} topic(s)). "
                  "Fix the path to pick up any changes to the document.")
            return index
        raise

    if index is not None and index.get("source_hash") == current_hash:
        # 2026-09-25: the category is asked for here too, not only when
        # extraction runs. It used to be set exclusively inside the
        # extraction branch below, so a product whose index predated the
        # category — or whose whitepaper had not changed — could never
        # acquire one, and Cognee being down made it impossible outright.
        #
        # The cost of missing it is not cosmetic. With no category the
        # image prompt says only "Subject: Fluffy roommate", nothing
        # anywhere states that the subject is a cat, and FLUX drew a dog.
        if not index.get("category") and extract_category_fn is not None:
            index = {**index, "category": _ask_for_category(extract_category_fn)}
            save_index(product_name, index)
        return index  # unchanged whitepaper — zero Cognee calls beyond this

    raw = extract_fn()
    extracted = parse_topics_response(raw)
    if raw.strip() and not extracted:
        # 2026-09-26: these two were indistinguishable. An answer that
        # parsed to nothing produced the same empty list as "there is
        # genuinely nothing new", so the run continued, the new document's
        # hash was recorded as processed, and the material in that answer
        # became unreachable — silently, with the index still holding the
        # topics from the previous version.
        #
        # The hash is deliberately NOT written in this case: leaving it at
        # the old value means the next run tries the extraction again
        # instead of believing this document has been handled.
        print(f"[error] The extractor returned {len(raw.strip())} characters but nothing in it "
              "could be read as a topic, so no topics were added. The document is NOT being "
              "marked as processed — fix the extraction and run again, or the new material "
              "stays invisible.\n"
              f"        First 200 characters of what came back:\n        {raw.strip()[:200]}")
        audit_note = index or {"product": product_name, "topics": []}
        return audit_note

    existing_topics = index["topics"] if index else []
    existing_names = {t["topic"].strip().lower() for t in existing_topics}
    genuinely_new = [t for t in extracted if t["topic"].strip().lower() not in existing_names]

    # A section that kept its name but had its text rewritten (see
    # brief_changed): not a new topic, but not unchanged either. Offered for
    # review rather than applied silently, because the brief is what reaches
    # the drafting and image prompts — the human confirms document-derived
    # text here for the same reason they confirm a new topic.
    fresh_by_name = {t["topic"].strip().lower(): t for t in extracted}
    changed = [
        {"topic": t["topic"], "brief": fresh["brief"]}
        for t in existing_topics
        if (fresh := fresh_by_name.get(t["topic"].strip().lower()))
        and brief_changed(t.get("brief", ""), fresh["brief"])
    ]

    approved_new = _review_topics_interactive(genuinely_new) if genuinely_new else []

    # Recall, not precision: what extraction turned down, offered to the human.
    # Asked after the new topics so the reviewer has seen what WAS found first,
    # which is the context that makes a gap in it obvious.
    skipped = [r for r in (skipped_fn() if skipped_fn else [])
               if r.get("topic", "").strip().lower() not in existing_names]
    if skipped:
        approved_new += [
            {**t, "used_count": 0} if "used_count" not in t else t
            for t in _review_skipped(skipped)
            if t["topic"].strip().lower() not in
            {x["topic"].strip().lower() for x in approved_new} | existing_names
        ]

    approved_changed = _review_topics_interactive(
        changed, heading="SECTION SUMMARIES THAT CHANGED IN THE DOCUMENT",
    ) if changed else []

    # used_count survives an update: the brief changed, the topic did not,
    # and product_assets/draft_generator pick least-used-first from it.
    updated_briefs = {t["topic"].strip().lower(): t["brief"] for t in approved_changed}
    merged = [
        {**t, "brief": updated_briefs.get(t["topic"].strip().lower(), t.get("brief", ""))}
        for t in existing_topics
    ] + [{**t, "used_count": 0} for t in approved_new]

    # Category: asked for only when we do not already have one, so a
    # whitepaper edit does not make the human re-confirm something that
    # has not changed.
    category = (index or {}).get("category", "")
    if not category and extract_category_fn is not None:
        category = _ask_for_category(extract_category_fn)

    new_index = {"product": product_name, "source_hash": current_hash,
                 "category": category, "topics": merged}
    save_index(product_name, new_index)
    return new_index


def load_category(product_name: str) -> str:
    """What kind of real-world thing this product is — "a soft-sided cat
    playpen", "an automatic pet feeder".

    2026-09-22. Image generation needs this because the product NAME is
    meaningless to an image model: given "Fluffy roommate", FLUX invented
    a glowing fur-ball and, another time, a pillow. A category is the
    bridge from an invented name to an object that actually exists and
    can therefore be drawn.

    Empty string when none has been confirmed yet — callers fall back to
    the product name, same as before this existed.
    """
    index = load_index(product_name)
    return (index or {}).get("category", "") if index else ""


# A category is a noun phrase an image model can draw — "a domestic cat".
# Anything much longer is the extractor answering a different question.
MAX_CATEGORY_CHARS = 80


def clean_category(proposed: str) -> str:
    """Keep a proposal only if it is actually a category.

    2026-09-26: asked for "five words or fewer, a concrete noun phrase",
    Cognee returned a two-thousand-character summary of the whole document
    — numbered sections, bold headings, the lot. It was accepted at the
    prompt and stored, and from there it went into every drafting and
    image prompt for this product. The drafting model then picked a topic
    from the list *inside the summary* rather than from the topic index.
    A malformed answer does not just fail to help; it crowds out the real
    inputs.

    Rejecting it here means the human is asked to type one instead, which
    is a five-second job. Showing them two thousand words to approve is
    how the bad value got in.
    """
    text = " ".join((proposed or "").split())
    if not text or len(text) > MAX_CATEGORY_CHARS or "\n" in (proposed or "").strip():
        return ""
    return text


def _ask_for_category(extract_category_fn: Callable[[], str]) -> str:
    """Propose a category and have a human confirm it. Never raises except
    on an interrupted review.

    A failed proposal still asks: the human can type the category
    themselves, which matters because the extractor needs Cognee and the
    review does not.
    """
    try:
        raw = extract_category_fn()
    except Exception as e:  # noqa: BLE001 — the proposal is optional, the answer is not
        print(f"[warn] Could not propose a category automatically ({e}).")
        raw = ""
    proposed = clean_category(raw)
    if raw and not proposed:
        print(f"[warn] The extractor answered with {len(raw.strip())} characters where a short "
              "noun phrase was asked for, so its answer is not being offered. Type one below.")
    try:
        return _review_category_interactive(proposed)
    except ReviewInterrupted:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Category review failed ({e}); continuing without one.")
        return ""


def _review_category_interactive(proposed: str) -> str:
    """One human decision, once per whitepaper. Confirmed rather than
    taken on trust because this string ends up in every image prompt for
    this product — a wrong category is wrong in every picture, which is
    the same reason new topics and new draft labels are confirmed too.
    """
    print("\n" + "=" * 60)
    print("PRODUCT CATEGORY")
    print("-" * 60)
    print("What the product IS, in words an image generator can draw — a short")
    print("noun phrase like 'a domestic cat' or 'an automatic pet feeder'.")
    print("Not the product name, not a slogan. Without it, nothing tells the")
    print("image model what it is drawing.")
    print("-" * 60)
    if not proposed:
        # Nothing worth accepting, so do not offer to accept it. Asking
        # "[a]ccept (nothing extracted)?" made the one useful option —
        # typing it yourself — the non-obvious one.
        print("Nothing usable was extracted. Type it yourself:")
        while True:
            try:
                typed = input("Category (blank to skip) > ").strip()
            except (EOFError, KeyboardInterrupt) as e:
                raise ReviewInterrupted("Category review interrupted") from e
            if not typed:
                print("Skipped — image generation will not know what the subject is.")
                return ""
            cleaned = clean_category(typed)
            if cleaned:
                return cleaned
            print(f"Too long — keep it under {MAX_CATEGORY_CHARS} characters, one line.")
    print(f"  Extracted: {proposed}")
    print("=" * 60)
    while True:
        try:
            choice = input("[a]ccept / [e]dit / [s]kip (no category) > ").strip().lower()
        except (EOFError, KeyboardInterrupt) as e:
            raise ReviewInterrupted("Category review interrupted") from e
        if choice == "a":
            return proposed.strip()
        if choice == "s":
            return ""
        if choice == "e":
            try:
                typed = input("Category (e.g. 'a soft-sided cat playpen'): ").strip()
            except (EOFError, KeyboardInterrupt) as e:
                raise ReviewInterrupted("Category review interrupted") from e
            if typed:
                return typed
            print("Empty — nothing changed.")
            continue
        print(f"{choice!r} isn't a valid choice — enter a, e or s.")


def pick_topics(index: dict[str, Any] | None, n: int) -> list[dict[str, Any]]:
    """Least-`used_count`-first, ties broken by list order (not random —
    same index state should always pick the same topics, so behavior is
    reproducible/debuggable). Repeats if there are fewer than `n` topics.
    Empty list if the index has no topics at all (caller falls back to
    the context generation — see draft_generator.py).

    2026-09-19 (code scan): used to wrap around (`i % len(ordered)`) when
    the index had fewer than `n` topics, so `--posts 5` against a 2-topic
    index produced 5 drafts covering the same 2 topics — duplicate posts,
    and record_topic_used() double-counting the same topic on publish.
    Returns fewer than `n` now instead: the caller gets one draft per
    genuinely distinct topic, which is the whole point of the index.
    """
    topics = (index or {}).get("topics") or []
    if not topics:
        return []
    ordered = sorted(range(len(topics)), key=lambda i: topics[i].get("used_count", 0))
    return [topics[i] for i in ordered[:n]]


def load_target(product_name: str) -> dict[str, str | None]:
    """2026-09-18 (real design review after an analysis session flagged
    that recommendations kept citing Google Trends' generic top regions,
    e.g. Romania/Indonesia/Peru, with no way to say "we're actually
    targeting the US"). `region` is the only dimension for now (testing
    phase); `audience` is free text. Both None if nothing's been set yet —
    callers should treat that as "no target, write generically", not an
    error. Stored on the same per-product file as the topic index since
    it's already this project's per-product settings store; a dedicated
    file wasn't worth it for two fields.
    """
    index = load_index(product_name)
    if not index:
        return {"region": None, "audience": None}
    return {"region": index.get("target_region"), "audience": index.get("target_audience")}


def save_target(product_name: str, region: str | None = None, audience: str | None = None) -> None:
    """Only touches the field(s) actually passed — an omitted argument
    (None) leaves whatever's already stored alone, so running with just
    `--target-region` doesn't wipe out a previously-set `--target-audience`
    (or vice versa). Creates the index file if this product has never
    extracted topics yet (target can be set on a brand-new product).
    """
    index = load_index(product_name) or {"product": product_name, "source_hash": None, "topics": []}
    if region is not None:
        index["target_region"] = region
    if audience is not None:
        index["target_audience"] = audience
    save_index(product_name, index)


def record_topic_used(product_name: str, topic_name: str) -> None:
    """Called once a post actually gets published (not at draft-write
    time, and not gated on the later checkpoint's engagement verdict — a
    rejected draft never reaches this call; "published" is the bar, per
    the 2026-09-15 design decision)."""
    index = load_index(product_name)
    if not index:
        return
    for t in index["topics"]:
        if t["topic"].strip().lower() == topic_name.strip().lower():
            t["used_count"] = t.get("used_count", 0) + 1
            break
    save_index(product_name, index)
