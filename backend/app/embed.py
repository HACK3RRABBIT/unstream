"""Resolve Spotify URLs without any API credentials.

Spotify's embed player (the widget any website can iframe) is served as a
public page at open.spotify.com/embed/<kind>/<id>. That page ships its
metadata — name, artists, cover art and the full track list — as JSON inside
a <script id="__NEXT_DATA__"> tag. No account, no token, no API key.

This is how most "Spotify downloader" websites work. Trade-off: it's an
undocumented page structure, so Spotify can change it at any time.
"""

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import Collection, ProviderError, Track

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.+?)</script>',
    re.DOTALL,
)
_URL_RE = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z]{2}(?:-[A-Z]{2})?/)?(track|album|playlist)/([A-Za-z0-9]+)"
)


def parse_url(url: str) -> tuple[str, str] | None:
    """Extract (kind, id) from a Spotify track/album/playlist URL, else None."""
    match = _URL_RE.search(url.strip())
    return (match.group(1), match.group(2)) if match else None


def _fetch_entity(kind: str, spotify_id: str) -> dict:
    url = f"https://open.spotify.com/embed/{kind}/{spotify_id}"
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        if exc.code == 404:
            raise ProviderError(
                f"Spotify says that {kind} doesn't exist (or it's private)."
            ) from exc
        raise ProviderError(f"Spotify embed page returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise ProviderError(f"Could not reach Spotify: {exc.reason}") from exc

    match = _NEXT_DATA_RE.search(html)
    if not match:
        raise ProviderError(
            "Could not read the Spotify embed page — its layout may have changed."
        )
    try:
        data = json.loads(match.group(1))
        entity = data["props"]["pageProps"]["state"]["data"]["entity"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ProviderError(
            "Spotify embed data was not in the expected format."
        ) from exc
    if not entity:
        raise ProviderError("Spotify embed page had no metadata for this item.")
    return entity


def _cover(entity: dict) -> str | None:
    # Album/playlist embeds use coverArt.sources; track embeds moved the
    # artwork under visualIdentity.image.
    sources = (entity.get("coverArt") or {}).get("sources") or []
    if sources:
        return max(sources, key=lambda s: s.get("width") or 0).get("url")
    images = (entity.get("visualIdentity") or {}).get("image") or []
    if images:
        return max(images, key=lambda s: s.get("maxWidth") or 0).get("url")
    return None


def _id_from_uri(uri: str) -> str:
    # "spotify:track:4uLU6..." -> "4uLU6..."
    return uri.rsplit(":", 1)[-1] if uri else ""


def _release_year(entity: dict) -> str:
    iso = (entity.get("releaseDate") or {}).get("isoString") or ""
    return iso[:4]


def _preview_url(entity: dict) -> str | None:
    # 30-second clip Spotify ships for embed players, when available.
    return (entity.get("audioPreview") or {}).get("url") or None


def _tracklist_track(
    item: dict, position: int, album_name: str, cover_url: str | None, year: str
) -> Track:
    return Track(
        id=_id_from_uri(item.get("uri", "")) or f"pos{position}",
        title=item.get("title", "Unknown"),
        artists=[a.strip() for a in (item.get("subtitle") or "").split(",") if a.strip()],
        album=album_name,
        duration_ms=int(item.get("duration") or 0),
        cover_url=cover_url,
        track_number=position,
        release_date=year,
        preview_url=_preview_url(item),
    )


def resolve(kind: str, spotify_id: str) -> Collection:
    entity = _fetch_entity(kind, spotify_id)
    name = entity.get("name") or entity.get("title") or "Unknown"
    cover = _cover(entity)

    if kind == "track":
        artists = [a.get("name", "") for a in entity.get("artists") or []]
        track = Track(
            id=spotify_id,
            title=name,
            artists=[a for a in artists if a] or ["Unknown"],
            album=(entity.get("albumOfTrack") or {}).get("name", ""),
            duration_ms=int(entity.get("duration") or 0),
            cover_url=cover,
            release_date=_release_year(entity),
            preview_url=_preview_url(entity),
        )
        return Collection(
            kind="track",
            name=track.title,
            owner=", ".join(track.artists),
            cover_url=cover,
            tracks=[track],
        )

    items = entity.get("trackList") or []
    if not items:
        raise ProviderError(f"The {kind} embed page listed no tracks.")
    year = _release_year(entity)

    if kind == "album":
        # Album embeds carry the artist in "subtitle", not an artists array.
        owner = entity.get("subtitle") or ", ".join(
            a.get("name", "") for a in entity.get("artists") or [] if a.get("name")
        )
        # Album embeds don't repeat the artist on every row; fall back to it.
        tracks = []
        for i, item in enumerate(items, start=1):
            track = _tracklist_track(item, i, album_name=name, cover_url=cover, year=year)
            if not track.artists and owner:
                track.artists = [owner]
            tracks.append(track)
        return Collection(
            kind="album", name=name, owner=owner, cover_url=cover, tracks=tracks
        )

    # playlist — rows carry "title" and "subtitle" (artist names)
    tracks = [
        _tracklist_track(item, i, album_name="", cover_url=cover, year="")
        for i, item in enumerate(items, start=1)
    ]
    return Collection(
        kind="playlist",
        name=name,
        owner=entity.get("subtitle", ""),
        cover_url=cover,
        tracks=tracks,
    )
