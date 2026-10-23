"""An image model looks at the actual image (2026-10-22, decided with the user:
"圖片的分析給Gemma,其他只做文字的部分").

Until now no model ever looked at a post's image. Gemma wrote the prompt
for FLUX and the text models never knew a post had one — so advisors kept
proposing "add a photo of a cat" to posts that already had a photo of a
cat. Now Gemma (vision) describes each image and checks it; the text models
get that one-sentence description and are told images are handled
separately.

Gemma describes and checks only. It does not propose image tests: a cluster
tests one change a week, and an image change would be a second one.

Prompt and parser built from Gemma's real replies on two published images
(tests/test_image_review.py): it named the invented "modern light device"
in draft-98f2d720's image, which is exactly the failure worth catching —
the product is the cat.
"""
from __future__ import annotations

import base64
import os
import re
from typing import Any

import requests

IMAGE_REVIEW_MODEL = os.environ.get("IMAGE_REVIEW_MODEL", "gemma3:4b")
_ENDPOINT = os.environ.get("OLLAMA_CHAT_ENDPOINT", "http://localhost:11434/api/chat")

PROMPT = ('Look at this image. It will be published together with this post:\n"{post}"\n\n'
          "Answer with exactly these four lines and nothing else:\n"
          "DESCRIPTION: one sentence on what the image actually shows\n"
          "MATCHES: yes or no — does the image show what the post is about?\n"
          "TEXT: yes or no — is there any lettering, writing or symbols that look like words "
          "anywhere in the image?\n"
          "INVENTED: yes or no — does the image show a device, gadget or product that the post "
          "does not mention?")

# Field name after any list decoration. See .claude/skills/parse-what-models-return.
_FIELD = re.compile(r"^[\s\-*>#\d.)]*\**\s*(DESCRIPTION|MATCHES|TEXT|INVENTED)\s*\**\s*:\s*\**\s*(.*)$",
                    re.IGNORECASE)


def _yes_no(value: str) -> bool | None:
    m = re.match(r"\W*(yes|no)\b", value or "", re.IGNORECASE)
    return None if not m else m.group(1).lower() == "yes"


def parse(text: str | None) -> dict[str, Any]:
    """{description, matches, has_text, invented, parsed}. `parsed` False
    means the reply could not be read — distinct from an image that passed."""
    found: dict[str, str] = {}
    for line in (text or "").splitlines():
        m = _FIELD.match(line.strip())
        if m and m.group(1).upper() not in found:
            found[m.group(1).upper()] = m.group(2).strip().strip("*").strip()
    out = {"description": found.get("DESCRIPTION", ""),
           "matches": _yes_no(found.get("MATCHES", "")),
           "has_text": _yes_no(found.get("TEXT", "")),
           "invented": _yes_no(found.get("INVENTED", ""))}
    out["parsed"] = bool(out["description"]) and None not in (out["matches"], out["has_text"],
                                                             out["invented"])
    return out


def review(image: bytes | None, post_text: str) -> dict[str, Any] | None:
    """Gemma's read of one image, or None when there is no image. Never
    raises: a failed call comes back with parsed=False and the reason."""
    if not image:
        return None
    try:
        resp = requests.post(_ENDPOINT, timeout=300, json={
            "model": IMAGE_REVIEW_MODEL, "stream": False, "options": {"temperature": 0.2},
            "messages": [{"role": "user", "content": PROMPT.format(post=post_text),
                          "images": [base64.b64encode(image).decode()]}]})
        resp.raise_for_status()
        raw = resp.json()["message"]["content"]
    except (requests.RequestException, KeyError, ValueError) as e:
        return {"description": "", "matches": None, "has_text": None, "invented": None,
                "parsed": False, "raw": "", "error": f"{type(e).__name__}: {e}",
                "model": IMAGE_REVIEW_MODEL}
    return {**parse(raw), "raw": raw, "model": IMAGE_REVIEW_MODEL}


def problems(r: dict[str, Any] | None) -> list[str]:
    """What a reviewer should be told, in plain words. Both review surfaces
    show this list."""
    if not r:
        return []
    if not r.get("parsed"):
        return [f"The image model could not check this image ({r.get('error') or 'unreadable reply'})."]
    out = []
    if r.get("invented"):
        out.append("The image shows a device or product the post does not mention.")
    if r.get("has_text"):
        out.append("The image contains lettering — generated text is usually garbled.")
    if r.get("matches") is False:
        out.append("The image does not show what the post is about.")
    return out


def as_labels(r: dict[str, Any] | None) -> dict[str, Any]:
    """The fields carried on the draft, then the tracked post (LABEL_KEYS)."""
    if not r:
        return {}
    return {"image_description": r.get("description", ""), "image_check": {
        k: r.get(k) for k in ("matches", "has_text", "invented", "parsed", "model")}}
