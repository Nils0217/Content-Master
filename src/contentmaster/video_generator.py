"""Short video, composed rather than generated.

2026-09-23, the shape docs/WHITEPAPER.md has specified since the start:
an LLM writes a script, each beat becomes a still image, each line becomes
speech, and ffmpeg assembles them. Not a generative video model — those
are not yet good or fast enough for real marketing output, and composition
is an honest use of tools that do work.

Four stages, each verifiable on its own:

    1. script   post -> N beats, each a line of narration + a shot
    2. visuals  each shot -> image_generator (reused unchanged, including
                its no-text check and retries)
    3. voice    each line -> speech, and the real duration it came out as
    4. compose  one clip per beat, concatenated

Stage 2 is the reason for this shape. The image work already solved the
hard part — prompts a diffusion model can draw, and a check that rejects
prompts which would render garbled lettering — so a video is N of those
rather than a new problem. It also means a fix to image prompting improves
video for free.

Timing comes from the audio, never the other way round: each image is held
for exactly as long as its line takes to say, measured after the fact with
ffprobe. Asking a model to estimate how long a sentence takes to read, or
fixing a per-slide duration, both produce drift that compounds over a
clip.

Speech is macOS `say` today — zero install, zero memory, and this machine
has 8GB shared with Cognee and Ollama. It sounds synthetic. That is a
deliberate first step: the script, timing and composition stages are
identical whichever engine speaks, so proving the chain end to end comes
first and a better voice (Piper, ~60MB, runs in this footprint) is a
drop-in replacement for `speak()` alone.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

_LLM_ENDPOINT = os.environ.get("LLM_IMPROVE_ENDPOINT", "http://localhost:11434/v1/chat/completions")
_LLM_MODEL = os.environ.get("LLM_IMPROVE_MODEL", "llama3.2:3b")

# macOS voices, best-first. `say -v '?'` lists dozens; most are novelty
# voices ("Bad News", "Bells", "Boing") that would be absurd on a product
# post, so this is an explicit shortlist rather than whatever comes first.
_VOICE_PREFERENCE = ("Samantha", "Alex", "Daniel", "Karen")

_SCRIPT_INSTRUCTION = """Write a short video script for this marketing post.

Post: {post}
Product: {product}{category_line}

Break it into {lo} to {hi} beats. Each beat is one spoken line and one shot to show while it is spoken.

SAY: one sentence of narration, written to be heard rather than read — short, plain, no semicolons, no parentheses, no hashtags, no emoji, no URLs.
SHOW: what is on screen during that line, described as a photograph.

Rules for SHOW, which matter because each one becomes a generated still:
- The product must be visible and recognisable as what it actually is.
- Nothing that carries writing: no books, signs, screens, phones, packaging, labels, posters. Generated lettering comes out as nonsense.
- No abstract ideas. Name something a camera could see.
- Each beat must show something DIFFERENT. A video of the same shot repeated is a still image with extra steps.

The first beat has to earn the next two seconds, and the last should leave the viewer with one thing.

Reply with beats in exactly this format, separated by a line of three dashes:

SAY: ...
SHOW: ...
---

No numbering, no commentary."""

_BEAT_PATTERN = re.compile(r"^(SAY|SHOW)\s*:\s*(.*)$", re.IGNORECASE)


@dataclass
class Beat:
    say: str
    show: str
    image: bytes | None = None
    audio_path: Path | None = None
    seconds: float = 0.0


@dataclass
class Video:
    path: Path
    beats: list[Beat]
    seconds: float
    alt: str = ""
    warnings: list[str] = field(default_factory=list)


class VideoUnavailable(RuntimeError):
    """Composition could not happen — no ffmpeg, no speech, no images.

    Raised rather than returning a partial file: a video missing its audio
    or with one beat silently dropped looks finished, and the reviewer has
    no way to tell it is not what the script said.
    """


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def duration_of(path: Path) -> float:
    """Real length of a media file, from ffprobe. Every timing decision in
    this module reads from here rather than estimating.
    """
    result = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                   "-of", "csv=p=0", str(path)])
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def pick_voice() -> str | None:
    result = _run(["say", "-v", "?"])
    if result.returncode != 0:
        return None
    installed = {line.split()[0] for line in result.stdout.splitlines() if line.strip()}
    for name in _VOICE_PREFERENCE:
        if name in installed:
            return name
    return None


def speak(text: str, out_path: Path, voice: str | None = None) -> float:
    """One line of narration to an audio file. Returns its real duration.

    `say` writes AIFF; it is transcoded to AAC because that is what the
    final container wants and doing it per beat keeps the concat step
    dealing with one codec.
    """
    if not shutil.which("say"):
        raise VideoUnavailable("No speech synthesiser available (macOS `say` not found).")
    aiff = out_path.with_suffix(".aiff")
    cmd = ["say", "-o", str(aiff)]
    if voice:
        cmd += ["-v", voice]
    cmd.append(text)
    if _run(cmd).returncode != 0 or not aiff.exists():
        raise VideoUnavailable(f"Speech synthesis failed for: {text[:60]!r}")
    if _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff),
             "-c:a", "aac", "-b:a", "128k", str(out_path)]).returncode != 0:
        raise VideoUnavailable("Could not transcode narration to AAC.")
    aiff.unlink(missing_ok=True)
    return duration_of(out_path)


def parse_script(content: str) -> list[Beat]:
    """SAY/SHOW blocks into beats. A block missing either half is dropped
    rather than guessed at — a beat with no shot has nothing to display,
    and one with no line has no duration.

    Separators are treated as optional. Asked for "a line of three
    dashes", llama3.2:3b returns `-- ` — two dashes and a trailing space —
    and puts it *before* each block rather than between them. A parser
    that depends on punctuation the model was merely asked to produce is a
    parser that breaks on the next model. So a beat is also flushed
    whenever a second SAY arrives while one is already complete, which
    makes the separator a hint rather than a requirement.
    """
    beats, current = [], {}

    def flush() -> None:
        if current.get("SAY") and current.get("SHOW"):
            beats.append(Beat(say=current["SAY"], show=current["SHOW"]))
        current.clear()

    for raw in content.splitlines():
        line = raw.strip()
        if len(line) >= 2 and set(line) == {"-"}:
            flush()
            continue
        match = _BEAT_PATTERN.match(line)
        if not match:
            continue
        key, value = match.group(1).upper(), match.group(2).strip()
        if key == "SAY" and current.get("SAY") and current.get("SHOW"):
            flush()
        current[key] = value
    flush()
    return beats


def write_script(post_text: str, product: str = "", category: str = "",
                 min_beats: int = 3, max_beats: int = 5) -> list[Beat]:
    """The LLM decides how many beats and what they are, within a range.

    A range rather than a fixed count, for the same reason topic choice
    moved to the model: a hardcoded "always four beats" is a human guessing
    at what this particular post needs.
    """
    category_line = f"\nWhat it actually is: {category.strip()}" if category.strip() else ""
    prompt = _SCRIPT_INSTRUCTION.format(post=post_text, product=product or "the product",
                                        category_line=category_line, lo=min_beats, hi=max_beats)
    try:
        resp = requests.post(
            _LLM_ENDPOINT,
            json={"model": _LLM_MODEL, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.7},
            timeout=120,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
    except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError):
        return []
    return parse_script(content)[:max_beats]


def compose(beats: list[Beat], out_path: Path) -> Video:
    """One clip per beat, then concatenated.

    Per-beat clips rather than a single filter graph: a malformed beat
    fails on its own and says which one, where a single graph fails with
    an error naming no beat at all.
    """
    if not ffmpeg_available():
        raise VideoUnavailable("ffmpeg/ffprobe not on PATH — install ffmpeg to compose video.")
    usable = [b for b in beats if b.image and b.audio_path and b.seconds > 0]
    if not usable:
        raise VideoUnavailable("No beat has both a picture and narration.")

    warnings = []
    if len(usable) < len(beats):
        warnings.append(f"{len(beats) - len(usable)} beat(s) had no usable picture or audio "
                        "and were left out")

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        clips = []
        for i, beat in enumerate(usable):
            image_path = tmpdir / f"{i}.png"
            image_path.write_bytes(beat.image)
            clip = tmpdir / f"{i}.mp4"
            result = _run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-loop", "1", "-i", str(image_path), "-i", str(beat.audio_path),
                "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                # Even dimensions are required by yuv420p; scaling to a fixed
                # square also guarantees every clip matches, which concat needs.
                "-vf", "scale=1024:1024",
                "-c:a", "aac", "-b:a", "128k", "-shortest", str(clip),
            ])
            if result.returncode != 0 or not clip.exists():
                raise VideoUnavailable(f"Beat {i + 1} would not encode: {result.stderr[:200]}")
            clips.append(clip)

        listing = tmpdir / "clips.txt"
        listing.write_text("".join(f"file '{c}'\n" for c in clips))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        result = _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                       "-i", str(listing), "-c", "copy", str(out_path)])
        if result.returncode != 0 or not out_path.exists():
            raise VideoUnavailable(f"Could not join the clips: {result.stderr[:200]}")

    return Video(path=out_path, beats=usable, seconds=duration_of(out_path),
                 alt=" ".join(b.say for b in usable)[:1000], warnings=warnings)


def build(post_text: str, product: str = "", category: str = "",
          out_path: Path | None = None, workdir: Path | None = None) -> Video:
    """The whole chain: script, pictures, speech, composition.

    Returns a Video or raises VideoUnavailable. Never returns something
    half-made — see that exception's docstring for why.
    """
    from . import image_generator

    if not ffmpeg_available():
        raise VideoUnavailable("ffmpeg/ffprobe not on PATH — install ffmpeg to compose video.")

    beats = write_script(post_text, product=product, category=category)
    if not beats:
        raise VideoUnavailable("The model did not return a usable script.")

    voice = pick_voice()
    workdir = workdir or Path(tempfile.mkdtemp(prefix="cm-video-"))
    workdir.mkdir(parents=True, exist_ok=True)

    for i, beat in enumerate(beats):
        # The SHOW line is already a described photograph, so it goes in as
        # the post text for image_generator to turn into a FLUX prompt —
        # which brings its no-text check and retries along with it.
        prompt = image_generator.build_image_prompt(beat.show, product=product, category=category)
        beat.image = image_generator.generate_image(prompt)
        if beat.image is None:
            print(f"[warn] Beat {i + 1} produced no picture; it will be left out.")
            continue
        beat.audio_path = workdir / f"beat{i}.m4a"
        beat.seconds = speak(beat.say, beat.audio_path, voice=voice)

    out_path = out_path or (workdir / "video.mp4")
    return compose(beats, out_path)
