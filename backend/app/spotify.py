"""Resolve Spotify URLs (track / album / playlist) into track metadata.

Uses the official Spotify Web API via spotipy with the Client Credentials
flow — no user login needed, since we only read public catalog data.
"""

import os
import re

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

from .models import Collection, ProviderError, SearchResult, Track

_URL_RE = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z]{2}(?:-[A-Z]{2})?/)?(track|album|playlist)/([A-Za-z0-9]+)"
)


class SpotifyError(ProviderError):
    """User-facing error while talking to the Spotify API."""


def has_credentials() -> bool:
    return bool(os.getenv("SPOTIFY_CLIENT_ID") and os.getenv("SPOTIFY_CLIENT_SECRET"))


def _client() -> spotipy.Spotify:
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SpotifyError(
            "Spotify API credentials are missing. Create an app at "
            "developer.spotify.com/dashboard and put SPOTIFY_CLIENT_ID / "
            "SPOTIFY_CLIENT_SECRET in backend/.env"
        )
    auth = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
    return spotipy.Spotify(auth_manager=auth, retries=2)


def parse_url(url: str) -> tuple[str, str]:
    match = _URL_RE.search(url.strip())
    if not match:
        raise SpotifyError(
            "That doesn't look like a Spotify track, album or playlist URL."
        )
    return match.group(1), match.group(2)


def _cover(images: list[dict] | None) -> str | None:
    return images[0]["url"] if images else None


def _track_from_api(item: dict, cover_url: str | None = None) -> Track:
    album = item.get("album") or {}
    return Track(
        id=item["id"],
        title=item["name"],
        artists=[a["name"] for a in item["artists"]],
        album=album.get("name", ""),
        duration_ms=item["duration_ms"],
        cover_url=cover_url or _cover(album.get("images")),
        track_number=item.get("track_number", 0),
        release_date=album.get("release_date", ""),
    )


def _result_url(kind: str, spotify_id: str) -> str:
    return f"https://open.spotify.com/{kind}/{spotify_id}"


def search(query: str) -> list[SearchResult]:
    """Search the Spotify catalog for tracks, albums and playlists."""
    sp = _client()
    try:
        res = sp.search(query, type="track,album,playlist", limit=4)
    except spotipy.SpotifyException as exc:
        raise SpotifyError(f"Spotify search failed: {exc.msg}") from exc

    results: list[SearchResult] = []
    for item in (res.get("tracks") or {}).get("items") or []:
        if not item:
            continue
        artists = ", ".join(a["name"] for a in item["artists"])
        results.append(
            SearchResult(
                kind="track",
                id=item["id"],
                url=_result_url("track", item["id"]),
                name=item["name"],
                subtitle=artists,
                cover_url=_cover((item.get("album") or {}).get("images")),
            )
        )
    for item in (res.get("albums") or {}).get("items") or []:
        if not item:
            continue
        artists = ", ".join(a["name"] for a in item["artists"])
        year = (item.get("release_date") or "")[:4]
        results.append(
            SearchResult(
                kind="album",
                id=item["id"],
                url=_result_url("album", item["id"]),
                name=item["name"],
                subtitle=f"{artists}{' · ' + year if year else ''}",
                cover_url=_cover(item.get("images")),
            )
        )
    # The search API is known to return null playlist entries — filter them.
    for item in (res.get("playlists") or {}).get("items") or []:
        if not item:
            continue
        owner = (item.get("owner") or {}).get("display_name", "")
        results.append(
            SearchResult(
                kind="playlist",
                id=item["id"],
                url=_result_url("playlist", item["id"]),
                name=item["name"],
                subtitle=f"by {owner}" if owner else "",
                cover_url=_cover(item.get("images")),
            )
        )
    return results


def resolve(url: str) -> Collection:
    kind, spotify_id = parse_url(url)
    sp = _client()

    try:
        if kind == "track":
            item = sp.track(spotify_id)
            track = _track_from_api(item)
            return Collection(
                kind="track",
                name=track.title,
                owner=", ".join(track.artists),
                cover_url=track.cover_url,
                tracks=[track],
            )

        if kind == "album":
            album = sp.album(spotify_id)
            cover = _cover(album.get("images"))
            tracks = []
            page = album["tracks"]
            while page:
                for item in page["items"]:
                    # Album track objects omit the album key; fill it in.
                    item["album"] = {
                        "name": album["name"],
                        "images": album.get("images"),
                        "release_date": album.get("release_date", ""),
                    }
                    tracks.append(_track_from_api(item, cover_url=cover))
                page = sp.next(page) if page.get("next") else None
            return Collection(
                kind="album",
                name=album["name"],
                owner=", ".join(a["name"] for a in album["artists"]),
                cover_url=cover,
                tracks=tracks,
            )

        # playlist
        playlist = sp.playlist(spotify_id)
        tracks = []
        page = playlist["tracks"]
        while page:
            for entry in page["items"]:
                item = entry.get("track")
                # Skip local files, episodes and removed tracks.
                if not item or item.get("is_local") or item.get("type") != "track":
                    continue
                tracks.append(_track_from_api(item))
            page = sp.next(page) if page.get("next") else None
        return Collection(
            kind="playlist",
            name=playlist["name"],
            owner=playlist.get("owner", {}).get("display_name", ""),
            cover_url=_cover(playlist.get("images")),
            tracks=tracks,
        )
    except spotipy.SpotifyException as exc:
        if exc.http_status == 404:
            raise SpotifyError(
                "Spotify returned 404 — the item may be private or region-locked."
            ) from exc
        raise SpotifyError(f"Spotify API error: {exc.msg}") from exc
