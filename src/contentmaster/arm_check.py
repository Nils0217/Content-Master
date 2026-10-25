"""Does a cluster draft do what its arm requires? (2026-10-24, decided with the user.)

Cluster 1's first real run: the hypothesis was "include at least one
question", and the Thursday experiment post — which must NOT make the
change — asked "how do you establish trust with it?". A post on the wrong
side of the comparison silently erases the difference the week is meant
to measure, and only a human reading carefully caught it.

So every cluster draft is checked right after it is written: main-arm
posts must make the change, experiment posts must not. One that fails is
dropped before review and the slot is drafted again (run_cluster retries).

gemma3:4b (already loaded for images and advisor B). On the four real
cluster-1 drafts it answered correctly and identically twice each, under
a second per post once loaded — including "yes" on the Thursday post.
"""
from __future__ import annotations

import os
import re
from typing import Any

import requests

ARM_CHECK_MODEL = os.environ.get("ARM_CHECK_MODEL", "gemma3:4b")
_ENDPOINT = os.environ.get("OLLAMA_CHAT_ENDPOINT", "http://localhost:11434/api/chat")

PROMPT = ('This week\'s test changes one thing about how posts are written:\n"{test}"\n\n'
          'Here is a post:\n"{post}"\n\n'
          "Does this post make that change? Answer with exactly one line and nothing else:\n"
          "MAKES_CHANGE: yes or no")

_ANSWER = re.compile(r"MAKES[_ ]CHANGE\W*\s*(yes|no)\b", re.IGNORECASE)


def parse(text: str | None) -> bool | None:
    """True / False, or None when the reply says neither."""
    m = _ANSWER.search(text or "")
    if m:
        return m.group(1).lower() == "yes"
    bare = re.fullmatch(r"\W*(yes|no)\W*", (text or "").strip(), re.IGNORECASE)
    return None if not bare else bare.group(1).lower() == "yes"


def makes_change(post_text: str, test: str) -> dict[str, Any]:
    """{"makes_change": bool | None, "raw", "model"}. Never raises; a failed
    call is makes_change None with the error."""
    try:
        resp = requests.post(_ENDPOINT, timeout=120, json={
            "model": ARM_CHECK_MODEL, "stream": False, "options": {"temperature": 0},
            "messages": [{"role": "user", "content": PROMPT.format(test=test, post=post_text)}]})
        resp.raise_for_status()
        raw = resp.json()["message"]["content"]
    except (requests.RequestException, KeyError, ValueError) as e:
        return {"makes_change": None, "raw": "", "model": ARM_CHECK_MODEL,
                "error": f"{type(e).__name__}: {e}"}
    return {"makes_change": parse(raw), "raw": raw, "model": ARM_CHECK_MODEL}


def verdict(arm_is_experiment: bool, check: dict[str, Any]) -> str:
    """"ok" | "wrong_side" | "unchecked"."""
    answer = check.get("makes_change")
    if answer is None:
        return "unchecked"
    return "ok" if answer != arm_is_experiment else "wrong_side"


def describe(arm_is_experiment: bool, check: dict[str, Any] | None) -> str:
    """One plain line for a reviewer; both review surfaces show it."""
    if not check:
        return ""
    v = verdict(arm_is_experiment, check)
    if v == "ok":
        return ("Role check: passes — it does not make this week's change."
                if arm_is_experiment else "Role check: passes — it makes this week's change.")
    if v == "wrong_side":
        return ("Role check: FAILS — this experiment post makes the change it must leave out."
                if arm_is_experiment else "Role check: FAILS — this post does not make this week's change.")
    return f"Role check: could not be run ({check.get('error') or 'unreadable reply'}) — read it yourself."
