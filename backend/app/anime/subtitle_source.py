"""Subtitle sourcing for anime episodes: preserve, then recover.

The providers hand subtitles back in two shapes:

  * Nyaa fansubs embed soft subtitle tracks inside the mkv.
  * Anivexa/HLS supplies an external English subtitle URL on the stream.

Both paths can end with a requested language missing from the final file — a
HorribleSubs mkv whose track isn't tagged eng, an HLS source with no English
track, a Persian request with nothing to translate. Downloading has to cope
keylessly: there is no opensubtitles.com key here by default, and a subtitle
must never turn a successful download into a failure. The rule is *preserve
what the source offers, and recover what you can from the bytes you already
have*:

  1. Preserve — an embedded English or Persian track in the mkv is extracted
     and normalized to SRT so the muxer never loses it (the pre-fix bug was
     a missing-on-tag, silent bare-video fallback that shipped zero subs).
  2. Recover — a requested language that is still missing after preservation
     is derived from whatever English cue bytes exist (translate eng -> fas),
     or if even English is absent, the subtitles are simply not added.

Everything here is hermetic: extraction uses the local ffmpeg/ffprobe, and
translation reuses the existing keyless Google-Translate (SQLite-cached) in
``subtitle_translate``. A failure at any step degrades to "fewer subs", never
to a failed download.
"""

from pathlib import Path

from .. import downloader as _dl


def _probe_embedded(video: Path) -> list[tuple[int, str]]:
    """The per-type index + language tag of every embedded subtitle stream.

    ffprobe reports the global stream index, but ffmpeg's ``-map 0:s:N`` wants
    the per-type (subtitle-kind) index — the line's position among the
    subtitle-only probe output. The returned index is that per-type index.
    """
    import subprocess

    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "s",
                "-show_entries", "stream=index:stream_tags=language,title",
                "-of", "csv=p=0", str(video),
            ],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:  # noqa: BLE001 — probing is best-effort
        return []
    out: list[tuple[int, str]] = []
    for sub_idx, line in enumerate(proc.stdout.strip().splitlines()):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        lang = parts[1].lower()
        title = " ".join(parts[2:]).lower()
        if lang in ("eng", "en") or (not lang and "english" in title):
            out.append((sub_idx, "eng"))
        elif lang in ("fas", "fa", "per") or (not lang and ("persian" in title or "farsi" in title)):
            out.append((sub_idx, "fas"))
    return out


def extract_embedded(video: Path, dest: Path) -> dict[str, Path]:
    """Extract the mkv's embedded eng/fas subtitle tracks to normalized SRT.

    Returns {lang: srt_path} for the languages actually found. Extraction is
    best-effort: a missing per-type index, an unreadable stream, or ffmpeg
    failing just means that language isn't returned. The SRT files are written
    next to ``dest``'s stem and are safe to mux.
    """
    out: dict[str, Path] = {}
    from .subtitles import normalize_srt

    for idx, lang in _probe_embedded(video):
        srt = dest.with_name(f"{dest.stem}.{lang}.embedded.srt")
        try:
            _dl._run_ffmpeg(
                ["-i", str(video), "-map", f"0:s:{idx}", "-c:s", "srt"],
                srt, f"embedded {lang} extract",
            )
        except Exception:  # noqa: BLE001 — one language failing is not fatal
            srt.unlink(missing_ok=True)
            continue
        # Normalize VTT-in-SRT and drop empty tracks; keep only real cues.
        try:
            raw = srt.read_bytes()
            if not raw.strip():
                srt.unlink(missing_ok=True)
                continue
            text = normalize_srt(raw)
            if not text.strip():
                srt.unlink(missing_ok=True)
                continue
            srt.write_text(text)
        except Exception:  # noqa: BLE001
            srt.unlink(missing_ok=True)
            continue
        out[lang] = srt
    return out


def translate_eng(to: str, eng_srt: Path, dest: Path) -> Path | None:
    """Translate an English SRT into ``to`` using the keyless translator."""
    from .subtitle_translate import translate_srt_file

    try:
        return translate_srt_file(eng_srt, "fa", dest)
    except Exception:  # noqa: BLE001 — a failed translation drops the track
        return None