"""Layer — image generation (white paper §3 Phase 4, 2026-09-16).

Cloudflare Workers AI running FLUX.1-schnell — picked over Replicate/
Stability/OpenAI for this project's actual volume: 10,000 free Neurons/day
(a 1024x1024 image costs 57.6 Neurons, so ~173 free images/day, resets
daily, no expiry), which realistically means $0 ongoing cost for a
handful of images per post. See docs/LOG.md 2026-09-16 for the comparison
against the other options.

Silently disabled (returns None, never raises) when
CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN aren't set — same "optional,
graceful degradation" pattern as every other external dependency in this
project (Bluesky, Google Trends). A run without Cloudflare configured
just publishes text-only, exactly like today.
"""
from __future__ import annotations

import base64
import json
import os
import re

import requests

from .config import settings

_API_URL_TEMPLATE = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/black-forest-labs/flux-1-schnell"


def generate_image(prompt: str, seed: int | None = None) -> bytes | None:
    """One image, one prompt, one call. Returns raw PNG bytes, or None if
    Cloudflare isn't configured or the call fails for any reason — the
    caller (pipeline.py) always has a text-only fallback.
    """
    if not settings.cloudflare.configured:
        return None

    body: dict[str, object] = {"prompt": prompt}
    if seed is not None:
        body["seed"] = seed

    try:
        resp = requests.post(
            _API_URL_TEMPLATE.format(account_id=settings.cloudflare.account_id),
            headers={"Authorization": f"Bearer {settings.cloudflare.api_token}"},
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, json.JSONDecodeError):
        return None

    if not data.get("success"):
        return None
    image_b64 = (data.get("result") or {}).get("image")
    if not image_b64:
        return None
    try:
        return base64.b64decode(image_b64)
    except (ValueError, TypeError):
        return None

# 2026-09-22. What this replaced, twice.
#
# First the image prompt was the topic's brief passed through verbatim —
# "Cats require social interaction, territory marking, privacy, and
# safety..." — a list of concepts with nothing visual in it. Handed a
# sentence and no scene, a diffusion model renders the sentence, and one
# real image came back as a slide of garbled pseudo-text beside a photo
# of a cat.
#
# The fix after that was half a fix: the LLM wrote a scene and Python
# glued a fixed "no words, no letters, no captions..." suffix onto it.
# That suffix was written as if it were a negative prompt, but FLUX on
# Workers AI has no negative prompt — the API takes `prompt`, `steps` and
# `seed` — so every one of those words went into the *positive* prompt,
# where a CLIP-style encoder has no representation of "no" and mostly
# just sees `words`, `letters`, `captions`, `signage`, `logo`.
#
# So the model writes the whole prompt now. One call, one complete FLUX
# prompt, sent as-is. Nothing is concatenated onto it, because the thing
# being concatenated was the problem.
_PROMPT_INSTRUCTION = """Write an image prompt for FLUX, an AI photo generator, to illustrate this marketing post.

Post: {post}
Product: {product}{category_line}

Describe one image. What is in it is up to you — style, angle, media element, shot, someone using the product, the product alone, a moment of ordinary life around it, steps of interacting with the product.

Pick whatever genuinely suits THIS post rather than defaulting to a safe shot.

Four rules:

1. The product must be visibly the subject, and must be recognisable as what it actually is. Do not illustrate only a mood or a hazard with the product absent. Use the plain description of the product above rather than its brand name — FLUX does not know the name and will invent something.

2. FLUX cannot draw ideas. Replace abstract words — independence, safety, wellbeing, comfort, freedom — with something a physical, visible person could see.

3. Nothing in the scene may carry writing: no books, signs, screens, phones, packaging, labels, posters, newspapers, boxes, menus, printed clothing. FLUX renders writing as nonsense letters.

4. Unless you are deliberately choosing a cartoon, illustrated or fantasy style, the scene must obey physics: everything rests on something, weight sits where it would really sit, and nothing floats or is supported by nothing. If you do choose a stylised look, say so explicitly in the prompt.

Reply with the prompt only. One paragraph, under 60 words, no preamble."""

# Used only when the local LLM is unreachable. Kept deliberately short and
# with no negation in it at all.
_FALLBACK_PROMPT = ("A candid, natural-light photograph of {subject}, "
                    "shallow depth of field, plain uncluttered surroundings.")


# Words that mean rendered characters, and objects that always carry them.
# Checked against the prompt the model writes, because rule 3 in the
# instruction is not reliably obeyed: asked to illustrate "no risk", a 3B
# model's first instinct is to draw a note with "Risk assessment: 0"
# written on it — the same failure rule 2 describes (it cannot draw an
# idea, so it writes the idea down). A rule relies on the model behaving;
# a check does not.
_TEXT_TERMS = (
    "text", "word", "words", "letter", "letters", "lettering", "typography",
    "written", "handwritten", "writing", "writes", "phrase", "phrases",
    "caption", "captions", "title", "titles", "slogan", "inscription",
    "sign", "signs", "signage", "label", "labels", "logo", "logos", "branding",
    "poster", "posters", "banner", "billboard", "newspaper", "magazine", "menu",
    "book", "books", "notebook", "note", "notes", "sticky note", "post-it",
    "screen", "monitor", "smartphone", "packaging", "receipt", "certificate",
    "whiteboard", "blackboard", "chalkboard", "calendar", "diploma", "ticket",
    "envelope", "letterbox", "keyboard", "tablet", "laptop",
)
# A quoted string is the model asking FLUX to render those exact
# characters — the single strongest signal, and exactly what produced the
# image covered in nonsense sticky notes.
_QUOTED = re.compile(r"[\"\u201c\u2018][^\"\u201c\u201d\u2018\u2019]{2,}[\"\u201d\u2019]")


def prompt_violations(prompt: str) -> list[str]:
    """Reasons this prompt will probably produce garbled text. Empty = fine."""
    lowered = (prompt or "").lower()
    # Prefix match, not whole-word: "bookshelf" slipped past a
    # `\bbook\b` check and the image came back with a wall of books, every
    # spine covered in nonsense lettering. Compounds are how these words
    # actually appear — bookshelf, bookcase, signage, signpost, notepad,
    # labelled, screenshot, whiteboard.
    found = sorted({term for term in _TEXT_TERMS
                    if re.search(rf"\b{re.escape(term)}", lowered)})
    problems = []
    if found:
        problems.append("mentions things that carry writing: " + ", ".join(found))
    if _QUOTED.search(prompt or ""):
        problems.append("contains quoted words, which asks FLUX to render those exact characters")
    return problems


def _category_line(category: str) -> str:
    """What the product actually is, in words an image model knows.

    Omitted entirely when nothing has been confirmed — an empty "What it
    is:" line would invite the model to fill the blank itself, which is
    the behaviour this is here to stop.
    """
    category = (category or "").strip()
    return f"\nWhat it actually is: {category}" if category else ""


def build_image_prompt(post_text: str, subject_hint: str = "", product: str = "",
                       category: str = "") -> str:
    """The complete FLUX prompt, written by the local LLM from the post.

    Whatever the model returns is what gets sent. Python adds nothing —
    no style, no lighting, no mood. An earlier version asked the model for
    "the light" and "the mood" and got the same warm window light in every
    single image; the way to stop dictating a style is to stop naming one.
    """
    # Up to three attempts: the model is fast, the image is not, and a
    # rejected prompt costs a few seconds while a bad image costs a
    # regeneration and the reviewer's attention.
    for attempt in range(3):
        written = _llm_prompt(post_text, product, category)
        if not written:
            break
        problems = prompt_violations(written)
        if not problems:
            return written
        print(f"[retry {attempt + 1}/3] The image prompt would have produced text "
              f"({'; '.join(problems)}). Asking again.")
    if written:
        # Out of attempts. The prompt is still used rather than discarded —
        # a flawed scene beats no image, and review_image() still stands
        # between it and the post.
        print("[warn] Using an image prompt that may render text; check the image carefully.")
        return written
    subject = _first_clause(post_text) or _first_clause(subject_hint) or "the product in everyday use"
    return _FALLBACK_PROMPT.format(subject=subject)


def _first_clause(text: str) -> str:
    first = re.split(r"[.!?\n]", (text or "").strip())[0].strip()
    return first[:100]


def _llm_prompt(post_text: str, product: str = "", category: str = "") -> str | None:
    if not post_text.strip():
        return None
    endpoint = os.environ.get("LLM_IMPROVE_ENDPOINT", "http://localhost:11434/v1/chat/completions")
    model = os.environ.get("LLM_IMPROVE_MODEL", "llama3.2:3b")
    try:
        resp = requests.post(
            endpoint,
            json={"model": model,
                  "messages": [{"role": "user",
                                "content": _PROMPT_INSTRUCTION.format(
                                    post=post_text, product=product or "the product",
                                    category_line=_category_line(category))}],
                  "temperature": 0.7},
            timeout=60,
        )
        resp.raise_for_status()
        written = resp.json()["choices"][0]["message"]["content"].strip()
    except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError):
        return None
    # A preamble would be drawn as part of the picture.
    # Anything the model says before the description would be drawn as
    # part of the picture. "Generate an image of a cat" asks FLUX for a
    # picture containing the act of generating.
    written = re.sub(
        r"^(here(\'s| is)[^:]*:|image prompt:|prompt:|"
        r"(generate|create|produce|draw|render)\s+(an?\s+)?(image|photo|picture|illustration)"
        r"(\s+prompt)?(\s+of|\s+showing|\s+depicting|:)?\s*)",
        "", written, flags=re.IGNORECASE).strip().strip('"').strip()
    return written[:400] or None


def alt_text_for(prompt: str, limit: int = 300) -> str:
    """Accessibility text describing the picture. The prompt now IS a
    description of the photograph, so it can be used directly.
    """
    return (prompt or "").strip()[:limit]
