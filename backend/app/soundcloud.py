"""SoundCloud search via its public web API — no key, no account.

The SoundCloud web player talks to api-v2.soundcloud.com using a client_id
that ships inside soundcloud.com's public JS bundles. We scrape that id once
and cache it (the same trick yt-dlp uses), which buys full search parity
with the site: tracks, people, albums and playlists — far more than the
tracks-only `scsearch` that yt-dlp exposes.

If the id can't be scraped (markup change, network hiccup) the caller falls
back to yt-dlp's track search, so SoundCloud never disappears entirely.
"""

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import ProviderError, SearchResult

_API = "https://api-v2.soundcloud.com"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_SCRIPT_RE = re.compile(
    r'<script[^>]+src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"'
)
_CLIENT_ID_RE = re.compile(r'client_id\s*:\s*"([a-zA-Z0-9]{32})"')

_client_id: str | None = None
_client_id_lock = threading.Lock()


def _fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    with urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _scrape_client_id() -> str:
    html = _fetch("https://soundcloud.com/")
    scripts = _SCRIPT_RE.findall(html)
    # The id usually sits in one of the last bundles — search backwards.
    for src in reversed(scripts):
        try:
            match = _CLIENT_ID_RE.search(_fetch(src))
        except (URLError, OSError):
            continue
        if match:
            return match.group(1)
    raise ProviderError("Could not extract a SoundCloud client id.")


def _get_client_id(force_refresh: bool = False) -> str:
    global _client_id
    with _client_id_lock:
        if _client_id is None or force_refresh:
            _client_id = _scrape_client_id()
        return _client_id


def _api(path: str, **params) -> dict:
    for attempt in (0, 1):
        params["client_id"] = _get_client_id(force_refresh=attempt > 0)
        url = f"{_API}{path}?{urlencode(params)}"
        try:
            return json.loads(_fetch(url))
        except HTTPError as exc:
            # An expired client id answers 401/403 — re-scrape once.
            if exc.code in (401, 403) and attempt == 0:
                continue
            raise ProviderError(f"SoundCloud API returned HTTP {exc.code}") from exc
        except (URLError, json.JSONDecodeError) as exc:
            raise ProviderError(f"Could not reach SoundCloud: {exc}") from exc
    raise ProviderError("SoundCloud rejected the request.")


def _artwork(item: dict) -> str | None:
    url = item.get("artwork_url") or (item.get("user") or {}).get("avatar_url")
    # Default artwork is 100x100 ("-large"); the site swaps the suffix.
    return url.replace("-large.", "-t300x300.") if url else None


def _count(n: int | None, noun: str) -> str:
    return f"{n} {noun}{'s' if n != 1 else ''}" if n else ""


def _track_result(item: dict) -> SearchResult | None:
    # SNIP/BLOCK tracks are Go-only (DRM) — a download would fail anyway.
    if item.get("policy") in ("SNIP", "BLOCK"):
        return None
    if not item.get("permalink_url"):
        return None
    return SearchResult(
        kind="track",
        id=str(item.get("id", "")),
        name=item.get("title", ""),
        subtitle=(item.get("user") or {}).get("username", ""),
        cover_url=_artwork(item),
        url=item["permalink_url"],
        source="soundcloud",
    )


def _user_result(item: dict) -> SearchResult | None:
    if not item.get("permalink_url"):
        return None
    followers = _count(item.get("followers_count"), "follower")
    return SearchResult(
        kind="artist",
        id=str(item.get("id", "")),
        name=item.get("username", ""),
        subtitle=followers or "On SoundCloud",
        cover_url=(item.get("avatar_url") or "").replace("-large.", "-t300x300.")
        or None,
        url=item["permalink_url"],
        source="soundcloud",
    )


def _set_result(item: dict, kind: str) -> SearchResult | None:
    if not item.get("permalink_url"):
        return None
    owner = (item.get("user") or {}).get("username", "")
    tracks = _count(item.get("track_count"), "track")
    return SearchResult(
        kind=kind,
        id=str(item.get("id", "")),
        name=item.get("title", ""),
        subtitle=" · ".join(p for p in (owner, tracks) if p),
        cover_url=_artwork(item),
        url=item["permalink_url"],
        source="soundcloud",
    )


def search(query: str) -> list[SearchResult]:
    """Tracks, people, albums and playlists — the four calls run in parallel."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        tracks = pool.submit(_api, "/search/tracks", q=query, limit=10)
        users = pool.submit(_api, "/search/users", q=query, limit=5)
        albums = pool.submit(_api, "/search/albums", q=query, limit=5)
        playlists = pool.submit(
            _api, "/search/playlists_without_albums", q=query, limit=5
        )

    results: list[SearchResult] = []
    for item in tracks.result().get("collection") or []:
        if r := _track_result(item):
            results.append(r)
    for item in users.result().get("collection") or []:
        if r := _user_result(item):
            results.append(r)
    for item in albums.result().get("collection") or []:
        if r := _set_result(item, "album"):
            results.append(r)
    for item in playlists.result().get("collection") or []:
        if r := _set_result(item, "playlist"):
            results.append(r)
    return results
