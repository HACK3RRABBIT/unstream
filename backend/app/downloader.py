"""Find a track's audio, download it, convert to mp3 and tag it.

Pipeline per track:
  1. If the track already points at a YouTube/SoundCloud page (source_url),
     download that directly. Otherwise yt-dlp `ytsearch8:` for
     "<artists> - <title>" and pick the result whose duration is closest to
     the catalog duration (rejects live versions, hour-long mixes, etc.).
  2. Download bestaudio and let yt-dlp's ffmpeg postprocessor produce mp3.
     If the postprocessor leaves a non-mp3 audio file behind, convert it
     ourselves rather than failing.
  3. Embed ID3 tags + album art from the catalog metadata with mutagen.

Retries exclude the exact video that just failed, and the final attempt
searches SoundCloud instead of YouTube, so one broken upload never sinks
the track.
"""

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable
from urllib.request import urlopen

from mutagen.id3 import APIC, ID3, TALB, TDRC, TIT2, TPE1, TPE2, TRCK
from yt_dlp import YoutubeDL

from .models import Track

# A candidate must be within this many seconds of the catalog duration.
MAX_DURATION_DRIFT = 20

# Extensions the manual ffmpeg fallback will happily convert.
_AUDIO_EXTS = {".webm", ".m4a", ".opus", ".ogg", ".aac", ".wav", ".flac", ".mp4"}


class DownloadError(Exception):
    pass


def safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip().rstrip(".") or "track"


def _sibling_outputs(dest: Path) -> list[Path]:
    """Every file yt-dlp may have produced for this stem (dest.<ext>)."""
    if not dest.parent.exists():
        return []
    prefix = dest.name + "."
    return [p for p in dest.parent.iterdir() if p.name.startswith(prefix)]


def _clean_partials(dest: Path) -> None:
    """Drop leftovers from a failed attempt so a retry starts clean.

    A stale .part or half-converted .webm makes yt-dlp resume a broken
    download, which is one way ffmpeg ends up with no mp3 to produce.
    """
    for path in _sibling_outputs(dest):
        path.unlink(missing_ok=True)


def _pick_candidate(entries: list[dict], target_seconds: float) -> dict:
    """Pick the search result whose duration best matches the catalog's."""
    if target_seconds <= 0:
        if entries:
            return entries[0]
        raise DownloadError("No results found")
    scored = []
    for entry in entries:
        duration = entry.get("duration")
        if not duration:
            continue
        drift = abs(duration - target_seconds)
        if drift <= MAX_DURATION_DRIFT:
            scored.append((drift, entry))
    if scored:
        return min(scored, key=lambda pair: pair[0])[1]
    if entries:
        # Nothing within tolerance — fall back to the top result.
        return entries[0]
    raise DownloadError("No results found")


def search_source(
    track: Track, exclude: set[str] = frozenset(), prefix: str = "ytsearch8"
) -> str:
    """Return the URL of the best-matching upload on YouTube or SoundCloud.

    `exclude` holds URLs that already failed for this track (e.g. a 403);
    a retry then picks the next-best candidate instead of hitting the same
    broken upload again. `prefix` selects the site: ytsearchN / scsearchN.
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,  # metadata only, don't resolve each video
        "noplaylist": True,
        "retries": 3,
        "socket_timeout": 15,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"{prefix}:{track.query}", download=False)
    entries = [e for e in (info.get("entries") or []) if e]
    usable = [e for e in entries if e.get("url") not in exclude] or entries
    chosen = _pick_candidate(usable, track.duration_ms / 1000)
    return chosen["url"]


def _ffmpeg_convert(source: Path, mp3: Path) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(source), "-vn", "-codec:a", "libmp3lame",
         "-q:a", "2", str(mp3)],
        capture_output=True,
        timeout=300,
    )
    if proc.returncode != 0 or not mp3.exists():
        tail = proc.stderr.decode(errors="replace").strip().splitlines()[-1:]
        raise DownloadError(f"ffmpeg conversion failed: {' '.join(tail)}")


def download_audio(
    url: str,
    dest: Path,
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """Download `url` as mp3 into `dest` (a path without extension)."""

    def hook(status: dict) -> None:
        if on_progress and status.get("status") == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            if total:
                on_progress(status.get("downloaded_bytes", 0) / total)

    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": str(dest) + ".%(ext)s",
        "noplaylist": True,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 15,
        "nopart": False,
        "overwrites": True,
        "progress_hooks": [hook],
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }
    with YoutubeDL(opts) as ydl:
        ydl.download([url])

    mp3 = dest.with_suffix(".mp3")
    if mp3.exists():
        return mp3

    # The postprocessor sometimes leaves the raw audio behind (odd container,
    # interrupted convert). Salvage it with a direct ffmpeg pass instead of
    # declaring the track failed.
    leftovers = [p for p in _sibling_outputs(dest) if p.suffix.lower() in _AUDIO_EXTS]
    if leftovers:
        source = max(leftovers, key=lambda p: p.stat().st_size)
        _ffmpeg_convert(source, mp3)
        source.unlink(missing_ok=True)
        return mp3
    raise DownloadError("no audio file was produced")


def embed_tags(mp3_path: Path, track: Track) -> None:
    tags = ID3()
    tags.add(TIT2(encoding=3, text=track.title))
    tags.add(TPE1(encoding=3, text=", ".join(track.artists)))
    tags.add(TPE2(encoding=3, text=track.artists[0] if track.artists else ""))
    tags.add(TALB(encoding=3, text=track.album))
    if track.track_number:
        tags.add(TRCK(encoding=3, text=str(track.track_number)))
    if track.release_date:
        tags.add(TDRC(encoding=3, text=track.release_date[:4]))
    if track.cover_url:
        try:
            with urlopen(track.cover_url, timeout=10) as resp:
                tags.add(
                    APIC(
                        encoding=3,
                        mime="image/jpeg",
                        type=3,  # front cover
                        desc="Cover",
                        data=resp.read(),
                    )
                )
        except OSError:
            pass  # cover art is nice-to-have
    tags.save(mp3_path)


def download_track(
    track: Track,
    out_dir: Path,
    on_progress: Callable[[str, float], None],
    attempts: int = 4,
    filename: str | None = None,
) -> Path:
    """Full pipeline for one track. Reports (stage, fraction) via callback.

    `filename` (no extension) lets the caller guarantee a unique name —
    two tracks sharing one stem would otherwise clobber each other's files
    mid-download when they run concurrently.

    Attempt order: the track's own source page if it has one, then YouTube
    search (excluding failed uploads), then SoundCloud as the last resort.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(filename or f"{', '.join(track.artists)} - {track.title}")
    dest = out_dir / stem

    if shutil.which("ffmpeg") is None:
        raise DownloadError("ffmpeg is not installed or not on PATH")

    failed_urls: set[str] = set()
    last_error: Exception | None = None
    for attempt in range(attempts):
        if attempt:
            on_progress("retrying", 0.0)
            time.sleep(2 * attempt)
        url = None
        try:
            on_progress("searching", 0.0)
            if attempt == 0 and track.source_url:
                url = track.source_url
            elif attempt == attempts - 1:
                url = search_source(track, exclude=failed_urls, prefix="scsearch5")
            else:
                url = search_source(track, exclude=failed_urls, prefix="ytsearch8")

            _clean_partials(dest)
            on_progress("downloading", 0.0)
            mp3 = download_audio(
                url, dest, lambda frac: on_progress("downloading", frac)
            )

            on_progress("tagging", 1.0)
            embed_tags(mp3, track)
            return mp3
        except Exception as exc:
            last_error = exc
            if url:
                failed_urls.add(url)
    raise DownloadError(
        f"Failed after {attempts} attempts: {last_error}"
    ) from last_error
