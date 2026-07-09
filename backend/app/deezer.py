"""Search and resolve via Deezer's public API — no key, no account.

Deezer exposes its catalog (search, tracks, albums, playlists) at
api.deezer.com without any authentication, which makes it a perfect
metadata source for in-app search. The downloader only needs
artist + title + duration, so where the metadata comes from doesn't
matter — the audio is found on YouTube either way.
"""

import json
import re
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import Collection, ProviderError, SearchResult, Track

_API = "https://api.deezer.com"
_URL_RE = re.compile(
    r"deezer\.com/(?:[a-z]{2}/)?(track|album|playlist)/(\d+)"
)


def is_deezer_url(url: str) -> bool:
    return _URL_RE.search(url) is not None


def _get(path: str, **params) -> dict:
    url = f"{_API}{path}"
    if params:
        url += "?" + urlencode(params)
    try:
        with urlopen(Request(url), timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except URLError as exc:
        raise ProviderError(f"Could not reach Deezer: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ProviderError("Deezer returned an unreadable response.") from exc
    if isinstance(data, dict) and data.get("error"):
        message = data["error"].get("message", "unknown error")
        raise ProviderError(f"Deezer API error: {message}")
    return data


def search(query: str) -> list[SearchResult]:
    results: list[SearchResult] = []

    for item in _get("/search/track", q=query, limit=4).get("data") or []:
        results.append(
            SearchResult(
                kind="track",
                id=str(item["id"]),
                url=item.get("link", f"https://www.deezer.com/track/{item['id']}"),
                name=item.get("title", ""),
                subtitle=(item.get("artist") or {}).get("name", ""),
                cover_url=(item.get("album") or {}).get("cover_medium"),
            )
        )

    for item in _get("/search/album", q=query, limit=4).get("data") or []:
        results.append(
            SearchResult(
                kind="album",
                id=str(item["id"]),
                url=item.get("link", f"https://www.deezer.com/album/{item['id']}"),
                name=item.get("title", ""),
                subtitle=(item.get("artist") or {}).get("name", ""),
                cover_url=item.get("cover_medium"),
            )
        )

    for item in _get("/search/playlist", q=query, limit=4).get("data") or []:
        owner = (item.get("user") or {}).get("name", "")
        count = item.get("nb_tracks")
        subtitle = " · ".join(
            part
            for part in (f"by {owner}" if owner else "", f"{count} tracks" if count else "")
            if part
        )
        results.append(
            SearchResult(
                kind="playlist",
                id=str(item["id"]),
                url=item.get("link", f"https://www.deezer.com/playlist/{item['id']}"),
                name=item.get("title", ""),
                subtitle=subtitle,
                cover_url=item.get("picture_medium"),
            )
        )
    return results


def _track_from_api(item: dict, album_name: str = "", cover_url: str | None = None) -> Track:
    album = item.get("album") or {}
    return Track(
        id=str(item["id"]),
        title=item.get("title", "Unknown"),
        artists=[(item.get("artist") or {}).get("name", "Unknown")],
        album=album.get("title", album_name),
        duration_ms=int(item.get("duration") or 0) * 1000,
        cover_url=album.get("cover_big") or cover_url,
        track_number=item.get("track_position", 0),
        release_date=(item.get("release_date") or "")[:4],
    )


def resolve(url: str) -> Collection:
    match = _URL_RE.search(url)
    if not match:
        raise ProviderError("That doesn't look like a Deezer URL.")
    kind, deezer_id = match.group(1), match.group(2)

    if kind == "track":
        item = _get(f"/track/{deezer_id}")
        track = _track_from_api(item)
        return Collection(
            kind="track",
            name=track.title,
            owner=", ".join(track.artists),
            cover_url=track.cover_url,
            tracks=[track],
        )

    if kind == "album":
        album = _get(f"/album/{deezer_id}")
        cover = album.get("cover_big")
        name = album.get("title", "")
        year = (album.get("release_date") or "")[:4]
        artist = (album.get("artist") or {}).get("name", "")
        tracks = []
        for i, item in enumerate((album.get("tracks") or {}).get("data") or [], 1):
            track = _track_from_api(item, album_name=name, cover_url=cover)
            track.cover_url = track.cover_url or cover
            track.track_number = track.track_number or i
            track.release_date = track.release_date or year
            tracks.append(track)
        return Collection(
            kind="album", name=name, owner=artist, cover_url=cover, tracks=tracks
        )

    # playlist — track list is paginated via an absolute "next" URL
    playlist = _get(f"/playlist/{deezer_id}")
    cover = playlist.get("picture_big")
    tracks_page = playlist.get("tracks") or {}
    items = list(tracks_page.get("data") or [])
    next_url = tracks_page.get("next")
    while next_url:
        page = json.loads(urlopen(Request(next_url), timeout=15).read().decode())
        items.extend(page.get("data") or [])
        next_url = page.get("next")
    tracks = [_track_from_api(item, cover_url=cover) for item in items]
    return Collection(
        kind="playlist",
        name=playlist.get("title", ""),
        owner=(playlist.get("creator") or {}).get("name", ""),
        cover_url=cover,
        tracks=tracks,
    )
