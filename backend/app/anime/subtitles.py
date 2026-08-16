"""Subtitle parsing, normalization and muxing for anime episodes.

The providers hand back subtitles in two shapes — Nyaa as an embedded stream
inside the fansub mkv, HiAnime as an external downloaded file (usually VTT
despite the .srt name). The Persian-subtitle stage needs a single, stable
representation to translate against, so this module normalizes either shape
into SRT cues whose timestamps are preserved *verbatim* (translation never
touches the timeline), and provides the N-track muxer that writes the requested
soft-subtitle tracks into the final mp4.

The English-only path is deliberately unchanged: providers mux the original
subtitle directly; normalization and this machinery only come into play when a
Persian track is being generated.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class Cue:
    """One subtitle cue. `start`/`end` are the raw SRT timestamp strings
    ("00:00:01,000") so they round-trip exactly — translation never rewrites
    them."""

    index: int
    start: str
    end: str
    text: str


# SRT timestamps end in ",mmm"; VTT in ".mmm". Keep the separator with the
# timestamp so build_srt writes back what parse saw.
_TS_RE = re.compile(r"^\s*(.*?)\s*-->\s*(.*?)\s*$")


def parse_srt(text: str) -> list[Cue]:
    """Parse SRT text into cues. Blocks are blank-line separated; each block
    carries an optional index/id line, a `start --> end` line, then text.
    Indexes are renumbered; timestamps and multi-line text are kept verbatim."""
    cues: list[Cue] = []
    for block in re.split(r"\n[ \t]*\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        ts_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if ts_idx is None:
            continue
        m = _TS_RE.match(lines[ts_idx])
        if not m:
            continue
        start = m.group(1).strip().split()[0]
        end = m.group(2).strip().split()[0]
        body = "\n".join(lines[ts_idx + 1 :]).strip()
        cues.append(Cue(index=len(cues) + 1, start=start, end=end, text=body))
    return cues


def build_srt(cues: list[Cue]) -> str:
    """Rebuild SRT from cues, renumbering sequentially. Timestamps are the
    verbatim strings carried by the cues."""
    return "\n\n".join(
        f"{i}\n{cue.start} --> {cue.end}\n{cue.text}"
        for i, cue in enumerate(cues, 1)
    ) + ("\n" if cues else "")


def _parse_vtt(text: str) -> list[Cue]:
    """Parse VTT text into SRT-shaped cues. Drops the WEBVTT header, NOTE /
    STYLE / REGION blocks and per-cue ids; VTT `.mmm` timestamps become SRT
    `,mmm`. Any settings after the end time (`align:start …`) are dropped."""
    lines = text.splitlines()
    # Skip the WEBVTT header and any header lines up to the first timestamp.
    i = 0
    while i < len(lines) and "-->" not in lines[i]:
        i += 1
    blocks: list[list[str]] = []
    cur: list[str] = []
    for line in lines[i:]:
        if line.strip() == "":
            if cur:
                blocks.append(cur)
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)

    cues: list[Cue] = []
    for block in blocks:
        first = block[0].strip().lower()
        if first.startswith("note") or first in ("style", "region"):
            continue
        ts_idx = next((k for k, ln in enumerate(block) if "-->" in ln), None)
        if ts_idx is None:
            continue
        m = _TS_RE.match(block[ts_idx])
        if not m:
            continue
        start = m.group(1).strip().split()[0].replace(".", ",")
        end = m.group(2).strip().split()[0].replace(".", ",")
        body = "\n".join(block[ts_idx + 1 :]).strip()
        cues.append(Cue(index=len(cues) + 1, start=start, end=end, text=body))
    return cues


def parse_subtitles(raw: bytes) -> list[Cue]:
    """Parse raw subtitle bytes into SRT cues, handling both SRT and VTT."""
    text = raw.decode("utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.lstrip().startswith("WEBVTT"):
        return _parse_vtt(text)
    return parse_srt(text)


def normalize_srt(raw: bytes) -> str:
    """Normalize raw subtitle bytes into canonical SRT text."""
    return build_srt(parse_subtitles(raw))


def mux_subtitles(
    video: Path,
    tracks: list[tuple[str, Path]],
    dest: Path,
    on_progress: Callable[[str, float], None] | None = None,
) -> Path:
    """Mux zero or more soft-subtitle tracks into the mp4 with -c copy.

    `tracks` is a list of (language, subtitle-file) pairs, each an extra ffmpeg
    input mapped as one mov_text track with its language metadata. An empty
    list returns `video` untouched. Imported lazily to avoid a module cycle.
    """
    if not tracks:
        return video
    from ..downloader import _run_ffmpeg, _with_ext
    from .downloader import _video_has_audio

    out = _with_ext(dest, "mp4")
    tmp = _with_ext(dest, "subbed")
    args = ["-i", str(video)]
    for _lang, sub in tracks:
        args += ["-i", str(sub)]
    args += ["-c", "copy"]
    if _video_has_audio(video):
        args += ["-map", "0:v", "-map", "0:a"]
    else:
        args += ["-map", "0:v"]
    for i, (lang, _sub) in enumerate(tracks):
        args += [
            "-map", f"{i + 1}:0",
            "-c:s", "mov_text",
            f"-metadata:s:s:{i}",
            f"language={lang}",
        ]
    _run_ffmpeg(args, tmp, "subtitle mux")
    if video != out:
        video.unlink(missing_ok=True)
    tmp.rename(out)
    return out
