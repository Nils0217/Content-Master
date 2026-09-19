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

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from .config import settings
from .human_loop import ReviewInterrupted
from .slug import slugify

PLAYS_DIR = settings.project_root / "plays"
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
    """
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_index(product_name: str) -> dict[str, Any] | None:
    path = _index_path(product_name)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_index(product_name: str, index: dict[str, Any]) -> None:
    TOPICS_DIR.mkdir(parents=True, exist_ok=True)
    _index_path(product_name).write_text(json.dumps(index, indent=2, default=str))


def parse_topics_response(text: str) -> list[dict[str, str]]:
    """Parses Cognee's free-text response into structured topics. Expects
    (but doesn't strictly require) lines shaped like
    "Topic: <name> | Brief: <summary>" — a line that doesn't match this
    shape is skipped rather than guessed at; the human review step is
    what actually catches a bad/garbled extraction, not this parser.
    """
    topics = []
    for line in text.splitlines():
        line = line.strip("-* \t")
        if not line:
            continue
        m = re.match(r"topic\s*:\s*(.+?)\s*\|\s*brief\s*:\s*(.+)$", line, re.IGNORECASE)
        if m:
            topic, brief = m.group(1).strip(), m.group(2).strip()
            if topic and brief:
                topics.append({"topic": topic, "brief": brief})
    return topics


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


def _review_topics_interactive(new_topics: list[dict[str, str]]) -> list[dict[str, str]]:
    """Only called with genuinely *new* topics (ones not already in the
    index) — existing topics and their usage counts are never re-shown,
    matching "human doesn't need to spend a lot of time reviewing them".
    """
    print("\n" + "=" * 60)
    print(f"NEW TOPICS EXTRACTED FROM THE WHITEPAPER ({len(new_topics)})")
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


def sync_topics(
    product_name: str, whitepaper_path: str | Path, extract_fn: Callable[[], str],
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
        return index  # unchanged whitepaper — zero Cognee calls

    raw = extract_fn()
    extracted = parse_topics_response(raw)

    existing_topics = index["topics"] if index else []
    existing_names = {t["topic"].strip().lower() for t in existing_topics}
    genuinely_new = [t for t in extracted if t["topic"].strip().lower() not in existing_names]

    approved_new = _review_topics_interactive(genuinely_new) if genuinely_new else []
    merged = existing_topics + [{**t, "used_count": 0} for t in approved_new]

    new_index = {"product": product_name, "source_hash": current_hash, "topics": merged}
    save_index(product_name, new_index)
    return new_index


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
    time, and not gated on the later checkpoint's ctr verdict — a
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
