"""hianime.to scraper — the anime streaming site's own AJAX API.

hianime (formerly zoro.to, gogoanime's successor) exposes a JSON API behind
its pages. The video links sometimes come back AES-256-CBC-encrypted; the
key is published in the site's client bundle, and the anipy-cli/viu projects
keep a fetched copy in a GitHub raw file because it rotates. We do the same:
a keygen JSON is fetched once and cached, overridable via HIANIME_KEYGEN_URL
so a rotation can be re-pointed without a release.

This is the fragile member of the anime section — the same "a keyless source
can rot" trade the project already makes with YouTube and Genius (see
docs/DESIGN.md). Every network failure raises ProviderError so the provider
chain (providers.py) hops to the next source.

The site keys episodes by a *numeric* episode id that only exists on the
season's page, so resolving one episode is a small walk:

    resolve(season_title)  -> category slug        (/ajax/search/suggest)
    episode_stream(src, q) -> show id              (/category/<slug> page)
                            -> episode id           (/ajax/v2/episode/list/<showId>)
                            -> server id            (/ajax/v2/episode/servers?episodeId=)
                            -> m3u8 + subs          (/ajax/v2/episode/sources?id=<serverId>)
"""

import base64
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import httpx
from Crypto.Cipher import AES

from ..models import ProviderError
from .providers import EpisodeSource, EpisodeStream

BASE_URL = os.getenv("HIANIME_BASE_URL", "https://hianime.to")
# The scraped keygen JSON. Repoint via env when the site rotates its keys.
KEYGEN_URL = os.getenv("HIANIME_KEYGEN_URL", "")

_TIMEOUT = 20
_client = httpx.Client(
    headers={
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "application/json, text/html, */*",
        "Referer": BASE_URL,
    },
    timeout=_TIMEOUT,
    follow_redirects=True,
)

# Resolution names hianime's source endpoint returns / is asked with.
_RESOLUTIONS = ("360p", "480p", "720p", "1080p")
_DEFAULT_RESOLUTION = "720p"

# Subtitle languages we accept, Persian first (the project's focus), English
# as the fallback. The site mostly ships English soft-subs.
_PREFERRED_SUB_LANGS = ("English", "English (US)", "Persian", "Farsi")


@lru_cache(maxsize=1)
def _keygen() -> dict:
    """The AES key the site wraps its sources with, when it wraps them."""
    url = KEYGEN_URL or (
        "https://raw.githubusercontent.com/sdaqo/anipy-cli/"
        "refs/heads/key-gen/scripts/keygen/keygen.json"
    )
    try:
        resp = _client.get(url)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 — any failure means "provider down"
        raise ProviderError(f"Could not fetch hianime keygen: {exc}") from exc
    if not data.get("key"):
        raise ProviderError("hianime keygen is missing its key.")
    return data


def _decrypt(payload: dict) -> str:
    """Decrypt hianime's AES-256-CBC source payload into a JSON string.

    The site has alternated between encrypted and plaintext source responses.
    This tries the key when a `data` field looks encrypted, and returns the
    payload verbatim when it does not — so a plaintext response still works.
    """
    data = payload.get("data")
    if not isinstance(data, str) or data.startswith("{"):
        return json.dumps(payload)  # already plaintext
    try:
        raw = base64.b64decode(data)
        iv = raw[:16]
        cipher = AES.new(bytes.fromhex(_keygen()["key"]), AES.MODE_CBC, iv=iv)
        plain = cipher.decrypt(raw[16:])
        plain = plain.rstrip(b"\x00").rstrip(b"\x01\x02\x03\x04\x05\x06\x07\x08")
        return plain.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — a changed cipher shape reads as provider-down
        raise ProviderError("Could not decrypt hianime source.")


def _get(path: str, **params) -> httpx.Response:
    """One HTTP call. Every failure becomes ProviderError for the chain."""
    try:
        resp = _client.get(f"{BASE_URL}{path}", params=params or None)
        resp.raise_for_status()
        return resp
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"Could not reach hianime: {exc}") from exc


def _json(path: str, **params) -> dict:
    data = _get(path, **params).json()
    if data.get("status") is not True:
        raise ProviderError("hianime returned an error response.")
    return data


def _pick_episode_id(html: str, episode_number: int) -> str:
    """The site's numeric episode id for `episode_number` from the episode list."""
    # Each episode is an <a> with data-number (human number) and data-id (site id).
    for match in re.finditer(
        r'data-number="(\d+)"[^>]*data-id="(\d+)"', html
    ) | re.finditer(r'data-id="(\d+)"[^>]*data-number="(\d+)"', html):
        num, eid = match.groups()
        if int(num) == episode_number:
            return eid
    raise ProviderError(f"Episode {episode_number} not found on hianime.")


def _pick_server_id(html: str) -> str:
    """The server button carrying the actual stream, from the servers list."""
    # The list shows several mirrors; the first with data-id is the default.
    match = re.search(r'data-id="(\d+)"', html)
    if not match:
        raise ProviderError("No stream server found on hianime.")
    return match.group(1)


def _pick_subtitles(payload: dict) -> str | None:
    """The first preferred-language subtitle URL, if the source offers one."""
    for track in payload.get("data", {}).get("tracks", []) or []:
        if track.get("kind") != "captions":
            continue
        label = (track.get("label") or "").lower()
        if any(pref.lower() in label for pref in _PREFERRED_SUB_LANGS):
            return track.get("file")
    return None


class HianimeProvider:
    name = "hianime"
    streams_hls = True  # episode_stream returns an m3u8; yt-dlp does the fetch

    def available(self) -> bool:
        return True  # no credentials needed — the site itself is the gate

    def resolve(
        self, title: str, year: int | None, anilist_id: int | None = None
    ) -> EpisodeSource:
        """Find the season on hianime by name; return its category slug.

        `season` and `episode` stay 0 here — hianime keys a whole show as one
        entity, so per-episode resolution happens in `episode_stream`.
        """
        data = _json("/ajax/search/suggest", keyword=title)
        match = re.search(r'href="/category/([^"]+)"', data.get("html", ""))
        if not match:
            # The suggest endpoint can be thin; fall back to a full search page.
            page = _get("/search", keyword=title).text
            match = re.search(r'href="/category/([^"]+)"', page)
            if not match:
                raise ProviderError(f"'{title}' not found on hianime.")
        slug = match.group(1)
        return EpisodeSource(
            provider=self.name,
            anime_id=slug,
            anime_title=title,
            year=year,
            season=0,
            episode=0,
        )

    def episode_count(self, src: EpisodeSource) -> int | None:
        """The highest episode number hianime has listed for this show.

        AniList reports 0 for ongoing series; the provider's own episode list
        is the source of truth for how many have actually aired.
        """
        try:
            slug = quote(src.anime_id)
            show_match = re.search(r'data-id="(\d+)"', _get(f"/category/{slug}").text)
            if not show_match:
                return None
            show_id = show_match.group(1)
            html = _json(f"/ajax/v2/episode/list/{show_id}").get("html", "")
            numbers = [
                int(n) for n in re.findall(r'data-number="(\d+)"', html)
            ]
            return max(numbers) if numbers else None
        except ProviderError:
            return None  # a count is nice-to-have; the season can still fail softly

    def episode_stream(self, src: EpisodeSource, quality: str) -> EpisodeStream:
        """The m3u8 master URL + subtitle track for one episode number.

        `src.episode` is the human episode number (1..N); the site's numeric
        episode id is looked up from the season page.
        """
        slug = quote(src.anime_id)
        # 1. The category page carries the show id the episode list wants.
        show_match = re.search(r'data-id="(\d+)"', _get(f"/category/{slug}").text)
        if not show_match:
            raise ProviderError("Could not find hianime show id.")
        show_id = show_match.group(1)

        # 2. The episode list maps human numbers to site episode ids.
        episodes_html = _json(f"/ajax/v2/episode/list/{show_id}").get("html", "")
        episode_id = _pick_episode_id(episodes_html, src.episode)

        # 3. The servers list names the mirror carrying the real stream.
        servers_html = _json(
            "/ajax/v2/episode/servers", episodeId=episode_id
        ).get("html", "")
        server_id = _pick_server_id(servers_html)

        # 4. The sources call returns the m3u8 (possibly encrypted) + subs.
        sources = _json("/ajax/v2/episode/sources", id=server_id)
        payload = json.loads(_decrypt(sources))
        sources_list = payload.get("data", {}).get("sources") or []
        if not sources_list:
            raise ProviderError("hianime returned no stream sources.")
        # Prefer the requested resolution's variant if the master lists options;
        # the m3u8 master itself carries the per-resolution variants, so we ask
        # yt-dlp for the height — the master url is what we pass along.
        url = sources_list[0].get("file")
        if not url:
            raise ProviderError("hianime stream had no url.")

        return EpisodeStream(
            provider=self.name,
            url=url,
            headers={"Referer": BASE_URL},
            subtitle_url=_pick_subtitles(payload),
        )

    def download(self, *args, **kwargs) -> Path:
        # hianime hands the downloader an HLS url; anime/downloader.py does the
        # yt-dlp fetch. Nothing to override here.
        raise NotImplementedError("hianime streams download via anime/downloader.py")
