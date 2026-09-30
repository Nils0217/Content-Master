"""Read a source document's plain text locally — no Cognee, no network.

2026-09-19 (code scan): draft_generator.draft_posts() has always
documented a "no topic index yet (Cognee down, or nothing extracted)"
fallback that generates from a plain text blob instead. That fallback was
dead code: the 2026-09-15 refactor deleted step1_cognee_extract(), the
only thing that ever produced such a blob, but left the parameter and its
branch in place. So a Cognee outage skipped the LLM entirely and dropped
straight to a hardcoded template string — every draft identical, run
after run, with a healthy Ollama sitting right there unused.

This module is that blob's new source. Deliberately local-only: the whole
point is to still work when Cognee (or the network) is not there.

Format support is best-effort by design — an unreadable format returns ""
rather than raising, and the caller falls back one more level.

Reading is pandoc first, hand-rolled fallbacks second (2026-09-27).
`striprtf` threw the document's formatting away, and the formatting was
the structure: this whitepaper marks its sections with bold text, not
with heading styles, so a plain-text read could only find the seven
sections that happen to carry a number and missed the thirteen
subsections entirely — "Step 3: Activate Play Mode", "Productivity
Enhancement", "Comparing lifestyle risks". striprtf also emitted a U+FFFD
replacement character, and promoted three numbered *list items* to
headings.

pandoc was chosen over Docling (68 Python packages, and its RTF path
converts through LibreOffice first), Unstructured (33 packages, RTF goes
through a pandoc binary anyway, and it posts telemetry to
packages.unstructured.io by default), and macOS `textutil` (free and
correct, but drops formatting exactly like striprtf, and is macOS-only).
pandoc is one cross-platform binary, zero Python packages, and was the
only candidate with no false positives on the real document. It has no
PDF reader, so PDFs still go through `pypdf`.

Every pandoc call passes `--wrap=none` (a heading must not be split
across lines) and `-t markdown-smart` on the markdown path (the `smart`
output extension rewrites — as ---, mangling real text).

Nothing here is a hard dependency. Without pandoc, RTF falls back to
`striprtf` and heading extraction falls back to the numbered-line regex —
fewer sections, but nothing breaks. PDF needs `pypdf`, also optional
(`pip install pypdf`).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

# Extensions pandoc can read, mapped to the reader name it needs. Suffix
# and format name differ often enough (.md -> markdown, .htm -> html) that
# guessing from the extension is not safe. PDF is deliberately absent:
# pandoc has no PDF reader, so `pandoc --list-input-formats` has no pdf
# entry and a PDF must keep going to `pypdf`.
PANDOC_FORMATS: dict[str, str] = {
    ".rtf": "rtf",
    ".docx": "docx",
    ".odt": "odt",
    ".pptx": "pptx",
    ".epub": "epub",
    ".html": "html",
    ".htm": "html",
    ".tex": "latex",
    ".org": "org",
    ".rst": "rst",
    ".textile": "textile",
    ".ipynb": "ipynb",
}

# How long a single conversion may take. A whitepaper converts in well
# under a second; the timeout only exists so a pathological file cannot
# hang the pipeline behind a subprocess that never returns.
PANDOC_TIMEOUT_SECONDS = 60

# Formats that need no conversion: they already ARE the text, and a markdown or
# plain-text file read through pandoc would only come back rewritten. Reading
# them directly also means a machine with no pandoc still gets the full
# structure-aware extraction for these.
PLAIN_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".mdown", ".txt", ".text", ""})

# Default truncation for the *prompt* path only — small local models
# degrade badly on very long contexts, and the draft prompt only needs
# enough grounding to write a few short posts. Callers that need the whole
# document (Cognee ingestion) pass max_chars=None.
PROMPT_MAX_CHARS = 6000


def read_text(path: str | Path, max_chars: int | None = None) -> str:
    """Best-effort plain text for `path`. Returns "" for anything that
    cannot be read locally (missing file, unsupported format, missing
    optional dependency) — never raises, so a caller can treat "" as
    "no grounding available" and degrade one more level.

    `max_chars` truncates the result; None (the default) returns the whole
    document. Ingestion callers must NOT truncate — cognee_client feeds
    this straight into the knowledge graph, and silently dropping the tail
    of a whitepaper there would quietly shrink everything downstream.
    """
    path = Path(path)
    try:
        path = resolve_bundle(path)
        if not path.is_file():
            return ""
        suffix = path.suffix.lower()

        text = _pandoc(path, "plain")
        if text:
            pass
        elif suffix == ".rtf":
            from striprtf.striprtf import rtf_to_text

            text = rtf_to_text(path.read_text(errors="replace"))
        elif suffix == ".pdf":
            text = _read_pdf(path)
        else:
            # .txt/.md/.markdown and anything else that is plausibly plain
            # text. errors="replace" keeps a stray byte from failing the
            # whole read.
            text = path.read_text(errors="replace")
    except Exception:  # noqa: BLE001 — a fallback that can raise is not a fallback
        return ""

    text = text.strip()
    return text[:max_chars] if max_chars is not None else text


def resolve_bundle(path: Path) -> Path:
    """An .rtfd is a directory, not a file — point at the .rtf inside it.

    2026-09-25: a real run failed with "No such file or directory:
    'Demo-white paper/cat.rtf'". The document had not been deleted. Adding
    images to it in TextEdit turned it from `cat.rtf` into `cat.rtfd/`, a
    macOS bundle holding `TXT.rtf` plus the pictures — a directory where
    the code expected a file.

    Nothing crashed, which was the dangerous part: topic_index falls back
    to the cached index when the source cannot be read, so the run
    continued against a four-topic index built from the older, shorter
    version of the document (12k characters against the 22k now in the
    bundle). The new material was simply never seen.

    Handled here rather than by asking people to avoid TextEdit, since
    the bundle is what macOS produces by default the moment a picture is
    pasted in.
    """
    if path.is_dir() and path.suffix.lower() == ".rtfd":
        inner = path / "TXT.rtf"
        if inner.is_file():
            return inner
    return path


def pandoc_available() -> bool:
    """Whether the `pandoc` binary is on PATH. Everything that uses pandoc
    degrades without it, so this is a capability check, not a requirement.
    """
    return shutil.which("pandoc") is not None


def _pandoc(path: Path, to: str) -> str:
    """Convert `path` to `to` with pandoc. Returns "" for every reason it
    could not happen — pandoc missing, format pandoc cannot read, a
    conversion error, a timeout — so a caller can treat "" as "fall back".

    `--wrap=none` keeps each paragraph on one line: pandoc wraps at 72
    columns by default, which splits a long heading across two lines and
    makes it unrecognisable as a heading. On the markdown path `smart` is
    switched off because that output extension rewrites real Unicode as
    ASCII digraphs (— becomes ---, curly quotes become straight ones), and
    that text goes on to be published verbatim.
    """
    fmt = PANDOC_FORMATS.get(path.suffix.lower())
    if fmt is None or not pandoc_available():
        return ""
    target = "markdown-smart" if to == "markdown" else to
    try:
        done = subprocess.run(
            ["pandoc", "--wrap=none", "-f", fmt, "-t", target, str(path)],
            capture_output=True, text=True, timeout=PANDOC_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if done.returncode != 0:
        return ""
    return done.stdout


def read_markdown(path: str | Path, max_chars: int | None = None) -> str:
    """The document as markdown, so its formatting survives the read.

    This is what heading extraction needs and `read_text()` cannot give
    it: bold, italics, lists and tables are all structure, and in this
    whitepaper bold is *the* section marker. Returns "" when pandoc is
    unavailable or cannot read the format, so callers fall back to the
    plain-text path.
    """
    path = Path(path)
    try:
        path = resolve_bundle(path)
        if not path.is_file():
            return ""
        if path.suffix.lower() in PLAIN_MARKDOWN_SUFFIXES:
            text = path.read_text(errors="replace").strip()
        else:
            text = _pandoc(path, "markdown").strip()
    except Exception:  # noqa: BLE001 — a fallback that can raise is not a fallback
        return ""
    return text[:max_chars] if max_chars is not None else text


def _read_pdf(path: Path) -> str:
    """Optional — returns "" when `pypdf` is not installed (see module
    docstring; it is deliberately not a hard dependency).
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


# A heading is a short line that is not a sentence. Numbered ones
# ("3. Basic Operating Procedure") are unambiguous; unnumbered ones are
# only taken when they are short, have no terminal punctuation, and are
# followed by prose.
_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(?P<title>\S.{2,69})$")
_SENTENCE_END = (".", ",", ";", ":", "!", "?", "•")


def extract_headings(text: str, min_brief: int = 40) -> list[dict[str, str]]:
    """The document's own section headings, with the first real sentence
    of each section as its summary.

    2026-09-26, replacing the Cognee call. Headings are literal text in
    the file: this whitepaper carries "1. Introduction", "2. Required
    Inputs", "3. Basic Operating Procedure" and so on, and a regular
    expression finds them exactly. Asking a retrieval system for them
    returned a summary instead — it chunks documents on ingest and does
    not keep their structure, so it had no headings to return.

    Headings are also a better unit than "topics" for this pipeline. They
    are the divisions the author already chose, so they are neither
    invented nor interpreted, and a post grounded in one is grounded in a
    section that really exists.
    """
    lines = [line.strip() for line in (text or "").splitlines()]
    headings: list[dict[str, str]] = []
    seen: set[str] = set()

    numbered = [(i, m) for i, line in enumerate(lines)
                if (m := _NUMBERED_HEADING.match(line)) and not line.endswith(_SENTENCE_END)]
    for i, m in numbered:
        title = m.group("title").strip()
        key = title.lower()
        if key in seen or len(title.split()) > 12:
            continue
        brief = ""
        for following in lines[i + 1:i + 8]:
            if len(following) >= min_brief and not _NUMBERED_HEADING.match(following):
                brief = following
                break
        if not brief:
            continue
        seen.add(key)
        headings.append({"topic": title, "brief": brief})
    return headings


# A markdown line that is nothing but one bold run. Bold is ONE signal that a
# line is a heading, not the definition of one — see extract_doc_headings.
_BOLD_LINE = re.compile(r"^\*\*(?P<title>.+?)\*\*$")

# Inline markup to strip out of a heading or a brief. The text is prose lifted
# from the document, so it must read as prose — not as markdown source.
#
# Handled as paired delimiters rather than as a single character class
# (2026-09-29): a class that matched "an asterisk not preceded by a word
# character" stripped the opening star of *Felis catus* and left the closing
# one, so a brief published "belonging to the species Felis catus*."
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_MD_UNDER = re.compile(r"(?<!\w)__?([^_\n]+)__?(?!\w)")
_MD_SUPER = re.compile(r"\^([^\^\n]+)\^")          # 21^st^ floor -> 21st floor
_MD_TRAILING = re.compile(r"\\+\s*$")               # pandoc hard line break


def _strip_markup(line: str) -> str:
    """One pass of markdown removal, shared by titles and briefs."""
    line = _MD_TRAILING.sub("", line.replace("\xa0", " "))
    line = _MD_LINK.sub(r"\1", line)
    line = _MD_BOLD.sub(r"\1", line)
    line = _MD_ITALIC.sub(r"\1", line)
    line = _MD_UNDER.sub(r"\1", line)
    line = _MD_SUPER.sub(r"\1", line)
    # Runs of spaces are column padding from a table cell, not prose spacing.
    return " ".join(line.split())

# A bullet or ordered-list marker at the start of a line — the text after it is
# prose, the marker is not.
_LIST_MARKER = re.compile(r"^(?:[-*+•]|\d+[.)])\s+")

# Any letter or digit. A line with none is pure markup — a table rule or a
# horizontal rule — not a summary of anything.
_PROSE = re.compile(r"[^\W_]", re.UNICODE)

# A markdown link. The brief is read aloud and published, so it carries the
# link text and drops the URL — one brief arrived as "It can be challenging to
# [know when your cat is in pain](https://www.medvet.com/pet-in-pain/)".
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")

# An embedded image, which pandoc emits as a bare filename on its own line.
_IMAGE_LINE = re.compile(r"^(?P<file>[\w][\w .()-]*\.(?:webp|jpe?g|png|gif|tiff?|bmp))\s*¬?\s*$",
                          re.IGNORECASE)

# Structural labels that name a part of a document rather than a subject. Kept
# deliberately short: only labels that carry no material of their own.
_DOC_FURNITURE = frozenset({"abstract", "contents", "table of contents", "references",
                            "bibliography", "appendix", "acknowledgements",
                            "acknowledgments", "index", "glossary", "foreword", "preface"})

# A heading is short. 70 characters and 12 words both come from this project's
# own whitepaper: its longest real heading is "What Are The Effects Of Indoor
# Living On Cats?" at 9 words, and the bold slogan that is formatted exactly
# like a heading and is not one runs to 14.
MAX_HEADING_CHARS = 70
MAX_HEADING_WORDS = 12

# A numbered section heading — "3. Basic Operating Procedure". The document's
# own top level, and therefore the boundary of everything under it.
_TOP_LEVEL = re.compile(r"^\d+(?:\.\d+)*[.)]?\s")


# How much of a section a brief may carry. Briefs go into the drafting prompt,
# and before this they ran from 28 characters to 869: one section whose material
# was a single huge paragraph crowded out every other topic in the same prompt,
# on 3B-4B local models that degrade badly on long context. Trimmed at a
# sentence boundary, so a brief is always whole sentences.
MAX_BRIEF_CHARS = 400


def _brief_limit(text: str) -> str:
    """Whole sentences, up to MAX_BRIEF_CHARS."""
    if len(text) <= MAX_BRIEF_CHARS:
        return text
    window = text[:MAX_BRIEF_CHARS]
    for end in (". ", "? ", "! "):
        cut = window.rfind(end)
        if cut > MAX_BRIEF_CHARS // 2:
            return window[:cut + 1]
    return _shorten(text, MAX_BRIEF_CHARS)


def _shorten(text: str, limit: int) -> str:
    """Trim at a word boundary. A mid-word cut published "Comparing lifestyle
    risks, Wh…" as if the document had a section called Wh."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(", ", 1)[0]
    return cut.rstrip(" ,") + ", …"


def headings_for(path: str | Path, min_brief: int = 40) -> list[dict[str, str]]:
    """The document's sections — the single entry point for topic extraction.
    Markdown path first, plain text second.

    Two paths rather than one because they find different amounts, and the
    better one needs pandoc (2026-09-27). On this project's whitepaper the
    markdown path finds 41 sections and the plain-text path finds 7. The
    plain-text path is kept as the fallback, not deleted — without pandoc the
    pipeline still gets the numbered sections instead of nothing.
    """
    headings, _skipped = headings_and_skipped_for(path, min_brief=min_brief)
    return headings


def headings_and_skipped_for(
    path: str | Path, min_brief: int = 40,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """`headings_for` plus the lines it turned down and why.

    The plain-text fallback reports nothing skipped — without pandoc there is no
    formatting to reason about, so "everything that is not a numbered line" is
    the whole document and listing it would help nobody.
    """
    md = read_markdown(path)
    if md:
        headings, skipped = extract_doc_headings_with_skipped(md, min_brief=min_brief)
        if headings:
            return headings, skipped
    return extract_headings(read_text(path), min_brief=min_brief), []


def _clean_title(line: str) -> str:
    """A heading with its markdown emphasis removed. The section NUMBER is
    kept — it is the author's own ordering, and _clean_brief() would strip
    "1. " off "1. Introduction" as if it were a list marker.
    """
    return _strip_markup(line)


def _clean_brief(line: str) -> str:
    """Prose, with markdown markup removed — inline emphasis, and the list
    marker on a bulleted or numbered line.

    Returns "" for a line that is markup and nothing else: a table's rule row
    (`-----|-----`) or a horizontal rule. The first section whose prose lived
    inside a table picked up 52 dashes as its summary, which then went into the
    drafting prompt as if it were a description of the section.
    """
    line = _strip_markup(line)
    if line and not _PROSE.search(line):
        return ""
    return _LIST_MARKER.sub("", line).strip()


def _heading_shape(raw: str) -> str | None:
    """The title if this line is a heading, else None. Thin wrapper over
    _judge_line for the callers that do not care why a line was rejected.
    """
    title, reason = _judge_line(raw)
    return None if reason else title


def _judge_line(raw: str) -> tuple[str | None, str | None]:
    """`(title, None)` for a heading; `(text, reason)` for a line that looked
    like one and was rejected; `(None, None)` for a line that was never in the
    running.

    The middle case is the point (2026-09-29). Every threshold in this strategy
    was derived from one document, so on somebody else's document it will drop
    real sections — and a silent drop is unfixable, because the human reviewing
    the topics has no way to know something is missing. Naming the rejects and
    the reason turns precision into a recall problem a person can solve once,
    which is the only honest way to ship a tuned heuristic.

    Position and shape decide a heading here, not bold (2026-09-28). Bold alone
    missed 20 of 41 sections in this project's own whitepaper, because that
    document uses three conventions: the hand-written part bolds its headings, a
    pasted section bolds its headings too, and a second pasted section marks
    them with plain text and blank lines. The author also bolded three of four
    "Step" headings and three of four "Problem" headings — the first of each
    series was missed both times. A signal an author applies by hand is a signal
    an author forgets by hand.

    A line ending in a colon introduces what follows; it does not name a
    section. That separates "Generally positive signals:" and "Final
    recommendation:" from real headings, and both used to be collected.

    A short line ending in a full stop is a sentence, not a section name.
    "Every cat has different preferences." sat alone between two headings and
    was collected as one. The exception is a labelled item — "Problem: The cat
    ignores you." — where the colon marks the label and the document really does
    use those four as its Troubleshooting subsections.
    """
    if raw[:1].isspace():
        return None, None  # indented: a table cell or an indented block
    body = raw.strip()
    if not body or _IMAGE_LINE.match(body):
        return None, None
    if _LIST_MARKER.match(body) or body[0] in "|>":
        return None, None
    title = _clean_title(body)
    if not title or not _PROSE.search(title):
        return None, None
    words = len(title.split())
    if len(title) > MAX_HEADING_CHARS or words > MAX_HEADING_WORDS:
        # Only a line that is NEARLY short enough is worth putting in front of a
        # person. At 140 characters the skipped list filled up with whole
        # sentences — "Do not attempt to control the cat. Build a good
        # environment…" at 135 characters and 25 words — and a list nobody reads
        # recovers nothing.
        near = len(title) <= MAX_HEADING_CHARS * 1.3 and words <= MAX_HEADING_WORDS + 4
        # A long line that also ends in a full stop is a sentence twice over.
        # Without this the skipped list carried every one-line paragraph in the
        # document alongside the sections actually worth recovering.
        if near and not title.endswith("."):
            return title, f"is longer than a heading ({len(title)} characters, {words} words)"
        return None, None  # plainly a paragraph; not worth anyone's attention
    if title.endswith(":"):
        return title, "ends in a colon, so it reads as a lead-in rather than a section name"
    if title.endswith(".") and ":" not in title:
        return title, "ends in a full stop, so it reads as a sentence rather than a section name"
    return title, None


def _with_list(brief: str, raw: list[str], start: int, stop: int) -> str:
    """A brief that ends in a colon is introducing a list; the list is the
    section's actual content, so it is folded in.

    Without this, "2. Required Inputs" summarised itself as "Before attempting
    to interact with a cat, prepare the following:" — a section about eight
    specific items, described in a way that names none of them.
    """
    lead = brief
    j = start
    if not lead.endswith(":"):
        # The lead-in can be a LINE OF ITS OWN after the summary sentence, which
        # is how "5. Maintenance Requirements" is written: one sentence, then
        # "Minimum responsibilities include:", then seven bullets. Checking only
        # the summary meant that section described itself in nine words and
        # dropped all seven of the responsibilities it is about.
        while j < stop and not raw[j].strip():
            j += 1
        nxt = _clean_brief(raw[j]) if j < stop else ""
        if not nxt.endswith(":") or _LIST_MARKER.match(raw[j].strip()):
            return brief
        lead = brief + " " + nxt
        j += 1
    items = []
    for k in range(j, stop):
        line = raw[k].strip()
        if not line:
            continue  # pandoc writes a loose list, one blank line between items
        if not _LIST_MARKER.match(line):
            break     # the first line that is not an item ends the list
        item = _clean_brief(line)
        if item:
            items.append(item)
    if not items:
        return brief
    return _brief_limit(lead + " " + ", ".join(items))


def _is_prose(raw: str, min_brief: int) -> bool:
    if raw[:1].isspace() or _IMAGE_LINE.match(raw.strip()):
        return False
    return len(_clean_brief(raw)) >= min_brief


# A markdown ATX heading: "## Pricing Tiers". This is what pandoc emits when a
# document actually declares its structure — Word/Google Docs heading styles,
# a Markdown source file, a tagged PDF, HTML h1-h6.
_ATX = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*#*$")


def extract_doc_headings(md: str, min_brief: int = 40) -> list[dict[str, str]]:
    """Sections of a pandoc-produced markdown document.

    Two strategies, tried in order of how much the document itself tells us
    (2026-09-29). This ordering is the answer to "does every new document need
    the code changed?" — for a document that declares its own structure, no
    heuristic runs at all:

    1. The document states its headings. Word and Google Docs heading styles, a
       Markdown source, HTML h1-h6 and a tagged PDF all reach pandoc as real
       headings and come out as `#`/`##`. Level is explicit, so nothing is
       guessed: no length limit, no word ceiling, no bold, no blank-line rule.
       This is the common case for documents people bring, and it used to score
       ZERO topics, because `#` was discarded as noise before the heuristics
       ran — the tuned fallback was doing all the work while the reliable
       signal was thrown away.

    2. The document declares nothing. Written in TextEdit with manual bolding,
       or assembled by pasting from several sources — which is exactly this
       project's own whitepaper, and why the heuristics below exist at all.
       Every threshold in them (MAX_HEADING_WORDS, the labelled-item exception,
       the numbered-section boundary) was derived from that one document and
       should be read as a best effort, not a specification. Their output is
       reviewed by a human before anything is stored (topic_index.sync_topics),
       which is what makes an imperfect guess usable rather than dangerous.
    """
    headings, _skipped = extract_doc_headings_with_skipped(md, min_brief=min_brief)
    return headings


def extract_doc_headings_with_skipped(
    md: str, min_brief: int = 40,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """`(headings, skipped)`. `skipped` holds the lines that looked like
    headings and were turned down, each with the reason, so the human reviewing
    the topics can put back anything the heuristics got wrong.

    It is always empty on the declared-headings path: there, nothing is guessed,
    so there is nothing to second-guess.
    """
    declared = extract_atx_headings(md, min_brief=min_brief)
    if declared:
        return declared, []
    return _extract_by_position(md, min_brief=min_brief)


def extract_atx_headings(md: str, min_brief: int = 40) -> list[dict[str, str]]:
    """Headings the document declares, with their real nesting level.

    A heading with no prose of its own before the next heading of the same or
    shallower level lists what it contains instead — the same treatment the
    heuristic path gives a group, except the level is stated rather than
    inferred, so the boundary cannot be wrong.
    """
    raw = (md or "").splitlines()
    found = [(i, len(m.group("hashes")), _strip_markup(m.group("title")))
             for i, line in enumerate(raw) if (m := _ATX.match(line.strip()))]
    if not found:
        return []

    own: dict[int, str] = {}
    for pos, (i, _level, _title) in enumerate(found):
        stop = found[pos + 1][0] if pos + 1 < len(found) else len(raw)
        for j in range(i + 1, stop):
            if _is_prose(raw[j], min_brief):
                own[i] = _brief_limit(_with_list(_clean_brief(raw[j]), raw, j + 1, stop))
                break

    headings: list[dict[str, str]] = []
    seen: set[str] = set()
    for pos, (i, level, title) in enumerate(found):
        key = title.lower()
        if not title or key in seen or key in _DOC_FURNITURE:
            continue
        brief = own.get(i, "")
        if not brief:
            children = []
            for _, lv, t in found[pos + 1:]:
                if lv <= level:
                    break  # a sibling or an ancestor — this section ends here
                children.append(t)
            if not children:
                continue
            brief = _shorten("Covers: " + ", ".join(children), 300)
        seen.add(key)
        headings.append({"topic": title, "brief": brief})
    return headings


def _extract_by_position(
    md: str, min_brief: int = 40,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Strategy 2 — for a document that declares no headings at all.

    A line is a heading when it is shaped like one (_heading_shape) AND it
    stands apart: it is bold, or a blank line sits above it, or one sits below.
    Any of the three is enough, because no document in practice uses one
    convention throughout.

    A heading with no prose of its own before the next heading is a GROUP — it
    names a set of subsections rather than carrying material. Its brief lists
    what it covers, which is the one honest summary available for it and tells
    the drafting model what is underneath. A group with nothing under it is the
    document's title block and is dropped.
    """
    raw = (md or "").splitlines()
    judged = [_judge_line(line) for line in raw]
    shapes = [None if reason else title for title, reason in judged]
    # Lines that looked like headings and were turned down, with the reason.
    # Surfaced to the human reviewing the topics — see
    # headings_and_skipped_for().
    rejects: list[dict[str, str]] = []

    def note(i: int, text: str, reason: str) -> None:
        rejects.append({"topic": text, "line": i, "reason": reason})

    for i, (text, reason) in enumerate(judged):
        if reason:
            note(i, text or "", reason)

    def stands_apart(i: int) -> bool:
        if _BOLD_LINE.match(raw[i].strip()):
            return True
        above = raw[i - 1].strip() if i > 0 else ""
        below = raw[i + 1].strip() if i + 1 < len(raw) else ""
        return not above or not below

    def after_lead_in(i: int) -> bool:
        """True when the nearest line above ends in a colon: this line is that
        lead-in's payload, not a section. Catches the bold one-liner
        "Feed -> Clean -> Play -> Respect -> Observe -> Repeat." """
        for j in range(i - 1, -1, -1):
            prior = raw[j].strip()
            if prior:
                return prior.rstrip("\\").rstrip().endswith(":")
        return False

    idx = []
    for i, t in enumerate(shapes):
        if t is None:
            continue
        if after_lead_in(i):
            note(i, t, "sits directly under a line ending in a colon, so it is that "
                       "lead-in's content rather than a section of its own")
        elif not stands_apart(i):
            note(i, t, "is not set apart from the text around it — no blank line above "
                       "or below, and not bold")
        else:
            idx.append(i)

    # Own prose: the first real sentence between this heading and the next.
    own: dict[int, str] = {}
    for pos, i in enumerate(idx):
        stop = idx[pos + 1] if pos + 1 < len(idx) else len(raw)
        for j in range(i + 1, stop):
            if _is_prose(raw[j], min_brief):
                own[i] = _brief_limit(_with_list(_clean_brief(raw[j]), raw, j + 1, stop))
                break
        else:
            # Nothing unindented — the section's material lives in a table.
            # "Comparing lifestyle risks" is entirely a table, so without this
            # it looked like a group and borrowed the next section's name.
            for j in range(i + 1, stop):
                cell = _clean_brief(raw[j])
                if len(cell) >= min_brief:
                    own[i] = _brief_limit(cell)
                    break

    # The title block: the run of headings at the very top with no material of
    # their own. A document opens with its title and subtitle, and neither is a
    # section someone can write a post about.
    first = 0
    while first < len(idx) and idx[first] not in own:
        note(idx[first], shapes[idx[first]],
             "sits at the very top of the document with no text of its own, so it "
             "reads as the title or subtitle rather than a section")
        first += 1
    idx = idx[first:]

    headings: list[dict[str, str]] = []
    seen: set[str] = set()
    for pos, i in enumerate(idx):
        title = shapes[i]
        key = title.lower()
        if key in seen:
            continue
        if key in _DOC_FURNITURE:
            note(i, title, "is a document label (Abstract, References, Appendix…) rather than "
                           "a subject anyone can write about")
            continue
        brief = own.get(i, "")
        if not brief:
            children = []
            for k in idx[pos + 1:]:
                if k not in own or _TOP_LEVEL.match(shapes[k]):
                    break  # the next group, or the next numbered section
                children.append(shapes[k])
            if not children:
                note(i, title, "has no text of its own and nothing beneath it")
                continue
            brief = _shorten("Covers: " + ", ".join(children), 300)
        seen.add(key)
        headings.append({"topic": title, "brief": brief})

    # A brief for each reject too, found the same way, so promoting one in the
    # review is a single keystroke instead of retyping the section by hand.
    kept_lines = set(idx)
    for r in rejects:
        i = r.pop("line")
        if i in kept_lines:
            continue
        r["brief"] = ""
        for j in range(i + 1, min(i + 10, len(raw))):
            if _is_prose(raw[j], min_brief):
                r["brief"] = _clean_brief(raw[j])
                break
    taken = {h["topic"].lower() for h in headings}
    rejects = [r for r in rejects
               if r.get("topic") and r["topic"].lower() not in taken and "brief" in r]
    return headings, rejects


def images_for(path: str | Path) -> list[dict[str, str]]:
    """Which embedded image belongs to which section.

    pandoc emits an embedded image as a bare filename on its own line, in the
    place it sits in the document, so the nearest heading above it is the
    section it illustrates. No image-extraction library is involved: in an
    .rtfd bundle the files are already plain files on disk beside the text
    (see resolve_bundle), and this only says which section each one is for.
    """
    md = read_markdown(path)
    if not md:
        return []
    raw = md.splitlines()
    shapes = [_heading_shape(line) for line in raw]
    found, current = [], ""
    for i, line in enumerate(raw):
        if shapes[i] is not None:
            current = shapes[i]
        if m := _IMAGE_LINE.match(line.strip()):
            found.append({"file": m.group("file"), "section": current})
    return found


# --- plain-text fallback, used when pandoc is not installed ---------------

# A heading is a short line that is not a sentence. Numbered ones
# ("3. Basic Operating Procedure") are unambiguous; unnumbered ones are only
# taken when they are short, have no terminal punctuation, and are followed by
# prose. Without pandoc there is no bold and no reliable blank-line structure,
# so numbering is the only signal left.
_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(?P<title>\S.{2,69})$")
_SENTENCE_END = (".", ",", ";", ":", "!", "?", "•")


def extract_headings(text: str, min_brief: int = 40) -> list[dict[str, str]]:
    """The document's numbered section headings, read from plain text.

    The fallback for a machine with no pandoc. Finds strictly less than
    extract_doc_headings — 7 sections against 41 on this project's whitepaper,
    because every unnumbered subsection is invisible once formatting is gone.
    Kept rather than deleted so a missing binary costs sections, not the run.
    """
    lines = [line.strip() for line in (text or "").splitlines()]
    headings: list[dict[str, str]] = []
    seen: set[str] = set()

    numbered = [(i, m) for i, line in enumerate(lines)
                if (m := _NUMBERED_HEADING.match(line)) and not line.endswith(_SENTENCE_END)]
    for i, m in numbered:
        title = m.group("title").strip()
        key = title.lower()
        if key in seen or len(title.split()) > MAX_HEADING_WORDS:
            continue
        brief = ""
        for following in lines[i + 1:i + 8]:
            if len(following) >= min_brief and not _NUMBERED_HEADING.match(following):
                brief = following
                break
        if not brief:
            continue
        seen.add(key)
        headings.append({"topic": title, "brief": brief})
    return headings
