"""Resolve and search YouTube / SoundCloud through yt-dlp — no key, no account.

yt-dlp already knows how to read public YouTube and SoundCloud pages, so the
same dependency that fetches the audio can also act as a metadata provider:

  * resolve():  a video / track / playlist / set / album URL -> Collection
  * search_youtube() / search_soundcloud():  text query -> SearchResults

Tracks resolved here carry `source_url`, so the downloader grabs that exact
page instead of running a YouTube search for a match.
"""

import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError as YtdlpError

from .models import Collection, ProviderError, SearchResult, Track

# YouTube bot-checks datacenter IPs ("Sign in to confirm you're not a bot")
# long before it bothers a home connection, which is why downloads can work
# locally and fail on a VPS. Pointing this at a cookies.txt exported from a
# signed-in browser is the documented fix, so it lives in the environment: a
# server that starts failing needs a mounted file and a restart, not a
# release. A path that isn't there is ignored rather than passed on — a stale
# variable would otherwise fail every extraction the process ever makes.
COOKIEFILE = os.getenv("YTDLP_COOKIEFILE", "")
if COOKIEFILE and not Path(COOKIEFILE).is_file():
    COOKIEFILE = ""


def base_opts(**extra) -> dict:
    """Options every yt-dlp call in the project shares."""
    opts = {"quiet": True, "no_warnings": True, **extra}
    if COOKIEFILE:
        opts["cookiefile"] = COOKIEFILE
    return opts


_YOUTUBE_RE = re.compile(
    r"(?:music\.|www\.|m\.)?(?:youtube\.com/(?:watch\?|playlist\?)|youtu\.be/)"
)
_SOUNDCLOUD_RE = re.compile(r"(?:www\.|m\.|on\.)?soundcloud\.com/")

# Noise commonly appended to YouTube titles that has no place in an ID3 tag.
_TITLE_NOISE_RE = re.compile(
    r"""[\(\[\|]?\s*
        (official\s+(music\s+)?(video|audio|visualizer|lyric\s+video)
        |lyrics?\s*(video)?
        |audio|visuali[sz]er|videoclip|clip\s+officiel
        |h[dq]|4k|full\s+album)
        \s*[\)\]\|]?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def is_youtube_url(url: str) -> bool:
    return _YOUTUBE_RE.search(url) is not None


def is_soundcloud_url(url: str) -> bool:
    return _SOUNDCLOUD_RE.search(url) is not None


def is_supported_url(url: str) -> bool:
    return is_youtube_url(url) or is_soundcloud_url(url)


def _extract(url_or_query: str, *, flat: bool = True) -> dict:
    opts = base_opts(
        extract_flat="in_playlist" if flat else False,
        skip_download=True,
        retries=2,
        socket_timeout=15,
    )
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url_or_query, download=False)
    except YtdlpError as exc:
        raise ProviderError(f"Could not read that page: {exc}") from exc
    if not info:
        raise ProviderError("That page had no readable media on it.")
    return info


def _clean_title(title: str) -> str:
    cleaned = title
    for _ in range(3):  # titles stack suffixes: "... (Lyrics) [HD]"
        stripped = _TITLE_NOISE_RE.sub("", cleaned).strip(" -–—")
        if stripped == cleaned or not stripped:
            break
        cleaned = stripped
    return cleaned or title


def _clean_uploader(name: str) -> str:
    name = re.sub(r"\s*-\s*Topic$", "", name)  # YouTube Music auto-channels
    name = re.sub(r"VEVO$", "", name)
    return name.strip()


def _entry_artist_title(entry: dict) -> tuple[str, str]:
    """Best-effort (artist, title) from a video/track entry."""
    title = _clean_title(entry.get("title") or "Unknown")
    artist = (
        entry.get("artist")
        or entry.get("creator")
        or _clean_uploader(entry.get("uploader") or entry.get("channel") or "")
    )
    # "Artist - Title" uploads are the norm on YouTube; prefer the split
    # whenever the title itself names the artist.
    if " - " in title:
        left, right = title.split(" - ", 1)
        if left.strip() and right.strip():
            return left.strip(), right.strip()
    return artist or "Unknown", title


def _thumbnail(entry: dict) -> str | None:
    if entry.get("thumbnail"):
        return entry["thumbnail"]
    thumbs = entry.get("thumbnails") or []
    if thumbs:
        return max(thumbs, key=lambda t: (t.get("width") or 0)).get("url")
    return None


def _entry_url(entry: dict) -> str:
    return entry.get("webpage_url") or entry.get("url") or ""


def _track_from_entry(entry: dict, album: str = "", position: int = 0) -> Track:
    artist, title = _entry_artist_title(entry)
    return Track(
        id=str(entry.get("id") or _entry_url(entry) or position),
        title=title,
        artists=[artist],
        album=album,
        duration_ms=int(float(entry.get("duration") or 0) * 1000),
        cover_url=_thumbnail(entry),
        track_number=position,
        release_date=str(entry.get("upload_date") or "")[:4],
        source_url=_entry_url(entry),
    )


def resolve(url: str) -> Collection:
    info = _extract(url)

    if info.get("_type") == "playlist" or "entries" in info:
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise ProviderError("That playlist appears to be empty or private.")
        # Profile pages resolve as "Username (All)" / "(Tracks)" — drop the tab.
        name = re.sub(
            r"\s*\((?:All|Tracks|Popular tracks)\)$",
            "",
            info.get("title") or "Playlist",
        )
        owner = _clean_uploader(info.get("uploader") or info.get("channel") or "")
        if not owner:
            owner = next(
                (
                    _clean_uploader(e.get("uploader") or e.get("channel") or "")
                    for e in entries
                    if e.get("uploader") or e.get("channel")
                ),
                name,  # a profile page's tracks belong to the profile itself
            )
        # SoundCloud albums are "sets" too; treat sets/playlists alike.
        tracks = [
            _track_from_entry(entry, album=name, position=i)
            for i, entry in enumerate(entries, start=1)
        ]
        for track in tracks:
            # Flat entries don't always name their uploader — inherit it.
            if track.artists in ([], ["Unknown"]) and owner:
                track.artists = [owner]
        cover = _thumbnail(info) or next(
            (t.cover_url for t in tracks if t.cover_url), None
        )
        return Collection(
            kind="playlist",
            name=name,
            owner=owner,
            cover_url=cover,
            tracks=tracks,
        )

    track = _track_from_entry(info)
    return Collection(
        kind="track",
        name=track.title,
        owner=", ".join(track.artists),
        cover_url=track.cover_url,
        tracks=[track],
    )


def _search(
    prefix: str, source: str, query: str, limit: int, offset: int = 0
) -> list[SearchResult]:
    # `ytsearchN:` / `scsearchN:` only take a count, never a start position —
    # paging means asking for everything through the end of the page and
    # dropping the head. Flat extraction keeps that cheap.
    try:
        info = _extract(f"{prefix}{offset + limit}:{query}")
    except ProviderError:
        return []  # search is aggregated; one silent source is fine
    results = []
    for entry in (info.get("entries") or [])[offset:]:
        if not entry:
            continue
        # Skip obvious non-songs (mixes, full concerts) and unplayable rows.
        duration = entry.get("duration")
        if duration and duration > 25 * 60:
            continue
        artist, title = _entry_artist_title(entry)
        results.append(
            SearchResult(
                kind="track",
                id=str(entry.get("id") or _entry_url(entry)),
                name=title,
                subtitle=artist,
                cover_url=_thumbnail(entry),
                url=_entry_url(entry),
                source=source,
            )
        )
    return results


# Per-page quota. Every page re-extracts from the top (see _search), so the
# cost grows linearly with depth — past this point yt-dlp would eat the whole
# search budget and starve the catalog APIs, so it bows out and they page on.
PAGE_QUOTA = 12
MAX_DEPTH = 36


def search_youtube(query: str, page: int = 0) -> list[SearchResult]:
    offset = page * PAGE_QUOTA
    if offset >= MAX_DEPTH:
        return []
    return _search("ytsearch", "youtube", query, PAGE_QUOTA, offset)


def search_soundcloud(query: str, page: int = 0) -> list[SearchResult]:
    offset = page * PAGE_QUOTA
    if offset >= MAX_DEPTH:
        return []
    return _search("scsearch", "soundcloud", query, PAGE_QUOTA, offset)


def search(query: str, page: int = 0) -> list[SearchResult]:
    """YouTube and SoundCloud searched concurrently."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        yt = pool.submit(search_youtube, query, page)
        sc = pool.submit(search_soundcloud, query, page)
    return yt.result() + sc.result()
