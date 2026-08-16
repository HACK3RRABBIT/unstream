"""Video pipeline for anime episodes: provider plan -> mp4 with soft subs.

A video track's `source_url` carries a synthetic plan (anime://...) instead
of a real URL. The downloader parses it back into an EpisodeSource, resolves
it to a concrete stream through the provider chain (hoping a provider that
rot mid-season), fetches the bytes, and muxes soft subtitles into an mp4.

Two kinds of provider:

  * scraper (hianime): episode_stream() returns an m3u8 url + subtitle url.
    yt-dlp downloads the HLS stream natively (fragment retries come from
    base_opts), then ffmpeg muxes the subtitle track with -c copy — the
    codecs are already h264/aac, so it is a mux, not a re-encode.

  * MTProto (telegram, Phase 3): download() pulls the bytes itself and
    returns the mp4; this module just runs the ffmpeg pass after it.

The stages emitted ("searching", "downloading", "tagging") are the same
ones jobs.py already reports, so the download dock renders anime jobs with
no changes.
"""

import json
import re
import shutil
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled

from .. import downloader as audio
from ..downloader import Cancelled, DownloadError, _clean_partials, _run_ffmpeg, safe_filename, _with_ext
from ..models import Track
from ..ytdlp import base_opts
from .providers import EpisodeSource, EpisodeStream, QualityUnavailable

# "original" keeps the provider's own stream untouched (like the audio
# section's original) instead of asking for a specific resolution. 360p is
# not offered — anime torrents are released at 480p and up, so a 360 option
# would only ever fail to match.
VIDEO_QUALITIES = ("480", "720", "1080", "original")
DEFAULT_VIDEO_QUALITY = "original"


def parse_source_url(url: str) -> EpisodeSource:
    """Rehydrate an EpisodeSource from `anime://<provider>/<animeId>/<season>/<episode>`."""
    if not url.startswith("anime://"):
        raise DownloadError(f"Not an anime plan: {url}")
    rest = url[len("anime://") :]  # <provider>/<animeId>/<season>/<episode>
    provider, anime_id, season, episode = rest.split("/", 3)
    return EpisodeSource(
        provider=provider,
        anime_id=unquote(anime_id),
        anime_title="",
        year=None,
        season=int(season),
        episode=int(episode),
    )


def _pick_resolution(requested: str) -> str:
    """Validate a requested resolution, defaulting unknown values to original."""
    if requested in VIDEO_QUALITIES:
        return requested
    return DEFAULT_VIDEO_QUALITY


def _provider_named(name: str):
    """Find a provider by name from the registry."""
    from .providers import providers

    for provider in providers():
        if provider.name == name:
            return provider
    raise DownloadError(f"Provider '{name}' is unavailable.")


def _format_selector(quality: str) -> str:
    """The yt-dlp format string for a requested resolution.

    `original` takes the upload's best available stream. An explicit
    resolution (480/720/1080) is strict: it asks for exactly that height and
    has NO trailing unrestricted `/best` fallback, so a missing variant fails
    the download instead of silently upgrading to 720p/1080p.
    """
    if quality == "original":
        return "bestvideo+bestaudio/best"
    return f"bestvideo[height={quality}]+bestaudio/best[height={quality}]"


def _download_with_ytdlp(
    stream: EpisodeStream,
    dest: Path,
    on_progress: Callable[[float], None],
    should_cancel: Callable[[], bool] | None,
    quality: str,
) -> Path:
    """Fetch an HLS/direct stream with yt-dlp into a .mp4 container.

    The format selector picks the master's variant nearest (and at most) the
    requested height, then merges the video+audio into mp4. yt-dlp's native
    HLS download + FFmpegMerger postprocessor handle both steps.
    """

    def hook(status: dict) -> None:
        if should_cancel and should_cancel():
            raise DownloadCancelled()
        if on_progress and status.get("status") == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            if total:
                on_progress(status.get("downloaded_bytes", 0) / total)

    # An explicit resolution must be honored, not best-effort: see
    # _format_selector — no `/best` fallback that could silently upgrade.
    opts = base_opts(
        format=_format_selector(quality),
        outtmpl=str(dest) + ".%(ext)s",
        noplaylist=True,
        retries=5,
        fragment_retries=5,
        socket_timeout=15,
        nopart=False,
        overwrites=True,
        progress_hooks=[hook],
        http_headers=stream.headers or {},
        # The stream is already the video we were asked for; never let yt-dlp
        # go looking for a "better" match.
        merge_output_format="mp4",
    )
    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([stream.url])
    except DownloadCancelled as exc:
        raise Cancelled() from exc

    # yt-dlp merges to dest.mp4; if it left the raw video around (odd stream),
    # salvage it with a direct ffmpeg pass.
    mp4 = _with_ext(dest, "mp4")
    if mp4.exists():
        return mp4
    candidates = [
        p
        for p in dest.parent.iterdir()
        if p.name.startswith(dest.name + ".")
    ]
    if candidates:
        source = max(candidates, key=lambda p: p.stat().st_size)
        _run_ffmpeg(["-i", str(source), "-c", "copy"], mp4, "mux")
        return mp4
    raise DownloadError("no video file was produced")


def _fetch_subs(stream: EpisodeStream, dest: Path) -> Path | None:
    """Download the subtitle track next to the video, if one was offered."""
    if not stream.subtitle_url:
        return None
    sub = _with_ext(dest, "srt")
    try:
        import httpx

        resp = httpx.get(stream.subtitle_url, headers=stream.headers or {}, timeout=30)
        resp.raise_for_status()
        # VTT is fine for players but srt is the most portable; the site mostly
        # serves .vtt, which we keep as-is in an .srt-looking file only if it
        # parses — otherwise ship the .vtt unchanged.
        sub.write_bytes(resp.content)
        return sub
    except Exception:  # noqa: BLE001 — a subtitle is nice-to-have, like cover art
        return None


def _mux_subtitles(video: Path, sub: Path | None, dest: Path, language: str = "eng") -> Path:
    """Mux a soft subtitle track into the mp4 with -c copy (no re-encode).

    `video` is the downloaded episode (often already `dest.mp4`); when a
    subtitle track exists it is added by remuxing to a fresh output and
    replacing the original — ffmpeg cannot read and write the same file.
    """
    if not sub or not sub.exists():
        return video
    out = _with_ext(dest, "mp4")
    tmp = _with_ext(dest, "subbed")
    args = ["-i", str(video), "-i", str(sub), "-c", "copy"]
    if _video_has_audio(video):
        args += ["-map", "0:v", "-map", "0:a"]
    else:
        args += ["-map", "0:v"]
    args += ["-map", "1:0", "-c:s", "mov_text", "-metadata:s:s:0", f"language={language}"]
    _run_ffmpeg(args, tmp, "subtitle mux")
    if video != out:
        video.unlink(missing_ok=True)
    tmp.rename(out)
    return out


def _video_has_audio(video: Path) -> bool:
    """Quick probe: does the mp4 already carry an audio stream?"""
    import subprocess

    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
             "stream=index", "-of", "csv=p=0", str(video)],
            capture_output=True, timeout=30,
        )
        return bool(proc.stdout.strip())
    except Exception:  # noqa: BLE001 — if we can't tell, assume yes
        return True


def _probe_height(video: Path) -> int | None:
    """The actual video height of a finished file, via ffprobe.

    This is the source of truth for `served_quality` — never the requested
    quality or the filename, both of which can lie about what was downloaded.
    """
    import subprocess

    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=height", "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=30,
        )
        line = proc.stdout.strip()
        return int(line) if line else None
    except Exception:  # noqa: BLE001 — an unreadable file isn't worth failing on
        return None


def _check_served_quality(requested: str, served: int | None) -> None:
    """Refuse an explicit-quality download that wasn't actually served at that
    resolution.

    A torrent's title can lie: one labeled [480p] may hold a 720p file. The
    bytes on disk are the truth, so once the file exists we verify the probed
    height against the request. `original` accepts whatever the source
    released and is never checked. For an explicit request (480/720/1080) a
    served height that differs — or that couldn't be probed at all — raises
    QualityUnavailable, so the provider chain moves on to the next source at
    the SAME requested resolution instead of shipping the wrong file.
    """
    if requested not in ("480", "720", "1080"):
        return
    if served is None:
        raise QualityUnavailable(
            f"Requested {requested}p but the served video's height could not be verified."
        )
    if served != int(requested):
        raise QualityUnavailable(
            f"Requested {requested}p but the served video is {served}p."
        )


def download_video_track(
    track: Track,
    out_dir: Path,
    on_progress: Callable[[str, float], None],
    quality: str,
    filename: str | None,
    should_cancel: Callable[[], bool] | None,
    meta: dict | None = None,
) -> Path:
    """Resolve the episode's stream and download it as an mp4 with soft subs.

    Mirrors downloader.download_track's contract (stage, fraction) callbacks
    so jobs.py drives it unchanged: 'searching' resolves the provider plan,
    'downloading' fetches bytes, 'tagging' muxes the subtitle track.

    When `meta` is given, it is filled with the ground truth of what was
    served: `provider` (the one that actually produced the file, which may
    differ from the plan's after a fallback) and `served_quality` (the actual
    video height, probed from the finished file with ffprobe — never the
    requested quality, which the output can fail to honor).

    An explicit resolution is enforced after the file exists: if the probed
    height doesn't match the request (a mislabeled release), the download is
    treated as quality-unavailable and the chain tries the next provider at
    the same resolution rather than shipping the wrong file.
    """
    if shutil.which("ffmpeg") is None:
        raise DownloadError("ffmpeg is not installed or not on PATH")

    source = parse_source_url(track.source_url or "")
    resolution = _pick_resolution(quality)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(filename or f"{track.album} - {track.title}")
    dest = out_dir / stem

    audio._stop_if_cancelled(should_cancel)

    # Walk the provider chain: a provider that rots mid-season is hopped over,
    # so the retry can land on the next source without the user knowing one
    # existed. Failing providers are remembered so we don't retry them.
    failed: set[str] = set()
    last_error: Exception | None = None
    for attempt in range(2):
        if attempt:
            on_progress("retrying", 0.0)
            audio._stop_if_cancelled(should_cancel)

        for provider in _chain_excluding(source.provider, failed):
            try:
                on_progress("searching", 0.0)
                stream = provider.episode_stream(source, resolution)
                if not stream.url and stream.telegram_media is None:
                    continue
                _clean_partials(dest)
                on_progress("downloading", 0.0)
                if provider.streams_hls:
                    video = _download_with_ytdlp(
                        stream, dest,
                        lambda f: on_progress("downloading", f),
                        should_cancel, resolution,
                    )
                else:
                    # The provider fetches the bytes itself (Nyaa torrents).
                    video = provider.download(
                        stream, dest, resolution,
                        lambda f: on_progress("downloading", f),
                        should_cancel,
                        subs=track.subs,
                    )
                on_progress("tagging", 1.0)
                sub = _fetch_subs(stream, dest)
                final = _mux_subtitles(video, sub, dest)
                # The file now exists and is probed; enforce the requested
                # resolution BEFORE the track can be marked done. A release
                # whose real height differs from what its title claimed is a
                # quality mismatch, not a completed download — fall through to
                # the next provider at the same resolution.
                height = _probe_height(final)
                if meta is not None:
                    meta["provider"] = provider.name
                    meta["served_quality"] = f"{height}p" if height else None
                _check_served_quality(resolution, height)
                return final
            except (Cancelled, KeyboardInterrupt):
                _clean_partials(dest)
                raise
            except Exception as exc:  # noqa: BLE001 — per-provider failure
                failed.add(provider.name)
                last_error = exc
                _clean_partials(dest)

    if isinstance(last_error, QualityUnavailable):
        raise DownloadError(
            f"Requested quality {resolution}p is unavailable for this episode."
        ) from last_error
    raise DownloadError(
        f"Failed to download episode after trying all providers: {last_error}"
    ) from last_error


def _chain_excluding(primary: str, excluded: set[str]):
    """The provider chain in priority order, minus ones that already failed."""
    from .providers import providers

    chain = list(providers())
    # Put the plan's provider first, then the rest in configured order.
    chain.sort(key=lambda p: (p.name != primary,))
    return [p for p in chain if p.name not in excluded]
