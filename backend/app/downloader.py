"""Find a track on YouTube, download the audio, convert to mp3 and tag it.

Pipeline per track:
  1. yt-dlp `ytsearch5:` for "<artists> - <title>", pick the result whose
     duration is closest to the Spotify duration (rejects live versions,
     hour-long mixes, etc.).
  2. Download bestaudio and let yt-dlp's ffmpeg postprocessor produce mp3.
  3. Embed ID3 tags + album art from the Spotify metadata with mutagen.
"""

import re
import time
from pathlib import Path
from typing import Callable
from urllib.request import urlopen

from mutagen.id3 import APIC, ID3, TALB, TDRC, TIT2, TPE1, TPE2, TRCK
from yt_dlp import YoutubeDL

from .models import Track

# A candidate must be within this many seconds of the Spotify duration.
MAX_DURATION_DRIFT = 20


class DownloadError(Exception):
    pass


def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "track"


def _pick_candidate(entries: list[dict], target_seconds: float) -> dict:
    """Pick the search result whose duration best matches Spotify's."""
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
    raise DownloadError("No YouTube results found")


def search_youtube(track: Track, exclude: set[str] = frozenset()) -> str:
    """Return the URL of the best-matching YouTube video.

    `exclude` holds video URLs that already failed for this track (e.g. a
    403 from YouTube); a retry then picks the next-best candidate instead
    of hitting the same broken video again.
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
        info = ydl.extract_info(f"ytsearch5:{track.query}", download=False)
    entries = [e for e in (info.get("entries") or []) if e]
    usable = [e for e in entries if e.get("url") not in exclude] or entries
    chosen = _pick_candidate(usable, track.duration_ms / 1000)
    return chosen["url"]


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
    if not mp3.exists():
        raise DownloadError("ffmpeg did not produce an mp3 file")
    return mp3


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
    attempts: int = 3,
) -> Path:
    """Full pipeline for one track. Reports (stage, fraction) via callback.

    YouTube intermittently 403s or times out on individual videos, so the
    whole search→download flow is retried with backoff, excluding the video
    that just failed so the next attempt picks a different candidate.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_filename(f"{', '.join(track.artists)} - {track.title}")
    dest = out_dir / stem

    failed_videos: set[str] = set()
    last_error: Exception | None = None
    for attempt in range(attempts):
        if attempt:
            on_progress("retrying", 0.0)
            time.sleep(2 * attempt)
        url = None
        try:
            on_progress("searching", 0.0)
            url = search_youtube(track, exclude=failed_videos)

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
                failed_videos.add(url)
    raise DownloadError(
        f"Failed after {attempts} attempts: {last_error}"
    ) from last_error
