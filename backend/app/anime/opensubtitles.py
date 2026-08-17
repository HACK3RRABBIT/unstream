"""Optional OpenSubtitles English-subtitle fallback — completely key-gated.

OpenSubtitles is the one external subtitle source that can rescue an episode
whose streaming source shipped no embedded English subtitles (anineko,
animegg, and friends via the anivexa sidecar). It is deliberately *not* part
of the default path: without OPENSUBTITLES_API_KEY the module is inert, makes
no requests, and never fails a download.

When enabled it is consulted only when the user actually asked for subtitles
AND the serving provider offered no English track. The result is validated
(parseable SRT/VTT with real cues) before it is ever handed to the muxer, and
a failed lookup returns None — a subtitle is nice-to-have, like cover art.
"""

import os
from pathlib import Path
from urllib.parse import quote

import httpx

from .subtitles import parse_subtitles

# The account-level key from opensubtitles.com (free tier). Empty disables the
# whole module: no startup failure, no requests, no code path taken.
API_KEY = os.getenv("OPENSUBTITLES_API_KEY", "")
BASE_URL = "https://api.opensubtitles.com/api/v1"
_TIMEOUT = 20

_client = httpx.Client(
    headers={
        "Api-Key": API_KEY,
        "User-Agent": (
            "Unstream self-hosted anime downloader/1.0 "
            "(keyless metadata; https://github.com/HACK3RRABBIT/unstream)"
        ),
        "Content-Type": "application/json",
    },
    timeout=_TIMEOUT,
    follow_redirects=True,
    trust_env=False,
)


def available() -> bool:
    """Enabled? The module is inert — and never consulted — without a key."""
    return bool(API_KEY)


def _search(imdb_id: str | None, title: str, season: int, episode: int) -> str | None:
    """Find an English subtitle file id for one episode, or None.

    `imdb_id` (from the anivexa /map endpoint) is the strongest lookup key;
    falling back to title + season + episode. Returns the first file id whose
    attributes confirm the match — never guesses.
    """
    params: dict = {
        "languages": "en",
        "season_number": season,
        "episode_number": episode,
        "order_by": "download_count",
        "order_direction": "desc",
        "ai_translated": "exclude",
        "machine_translated": "exclude",
    }
    if imdb_id:
        params["imdb_id"] = imdb_id
    else:
        params["query"] = title
    try:
        resp = _client.get(f"{BASE_URL}/subtitles", params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 — a subtitle is never worth a failure
        return None
    for item in data.get("data") or []:
        attributes = item.get("attributes") or {}
        # The episode number in the attributes must match ours; a title fallback
        # result that disagrees is the wrong file, not a close enough one.
        if str(attributes.get("episode_number")) != str(episode):
            continue
        for file_id in (item.get("attributes") or {}).get("files") or []:
            if isinstance(file_id, dict) and file_id.get("file_id"):
                return str(file_id["file_id"])
    return None


def _download(file_id: str, dest: Path) -> Path | None:
    """Download the subtitle file and validate it parses with real cues.

    Validation happens here, before the muxer: a link that returns HTML, or a
    file with no cues, is discarded rather than muxed into an episode.
    """
    try:
        resp = _client.post(
            f"{BASE_URL}/download",
            json={"file_id": int(file_id)},
        )
        resp.raise_for_status()
        link = (resp.json() or {}).get("link")
        if not link:
            return None
        file_resp = httpx.get(
            link,
            headers={"User-Agent": _client.headers.get("User-Agent", "")},
            timeout=_TIMEOUT,
            follow_redirects=True,
            trust_env=False,
        )
        file_resp.raise_for_status()
    except Exception:  # noqa: BLE001
        return None
    if not parse_subtitles(file_resp.content):
        return None  # unreadable / no cues — never mux garbage
    sub = dest.with_name(dest.name + ".opensubtitles.srt")
    sub.write_bytes(file_resp.content)
    return sub


def fetch_english(
    imdb_id: str | None,
    title: str,
    season: int,
    episode: int,
    dest: Path,
) -> Path | None:
    """A validated English subtitle for one episode, or None.

    The whole module is a no-op when disabled; a keyed lookup that fails for
    any reason also returns None. `dest` is the episode's stem (no extension).
    """
    if not available():
        return None
    file_id = _search(imdb_id, title, season, episode)
    if not file_id:
        return None
    return _download(file_id, dest)
