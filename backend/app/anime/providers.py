"""Anime video providers — where an episode's actual video comes from.

AniList (anilist.py) supplies *metadata*: what exists, how many episodes each
season has. A provider supplies the *video*: it maps (anime title, season,
episode) to a concrete download, either an HLS stream URL (scrapers) or a
Telegram file. Multiple providers exist (hianime, later the Telegram bot) and
are tried in order — the first that can resolve wins, so a rotten source is
hopped over without the user knowing a provider existed.

The plan a season was downloaded with is encoded into each episode's
`source_url` as:

    anime://<provider>/<provider_anime_id>/<season>/<episode>

so the downloader can re-resolve lazily per episode and fall back mid-season
if a provider fails.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

# Order providers are tried, in priority order. Empty/unset names are skipped;
# a name whose provider is unavailable (no credentials, no session) is skipped.
#
# The *configured* order has two layers. An explicit ANIME_PROVIDER_ORDER wins
# verbatim. Otherwise the order is quality-aware (see order_for): 480p/720p —
# where the streaming sidecar (anivexa) is strongest — lead with anivexa, while
# 1080p/original — the torrent archive's permanent home turf — lead with Nyaa.
# Nyaa stays in the list either way: it is the complete, keyless, always-
# reachable archive, and hianime remains the stream fallback.
PROVIDER_ORDER = os.getenv("ANIME_PROVIDER_ORDER", "nyaa,anivexa,hianime")

# Order that works when the user never set ANIME_PROVIDER_ORDER. Low
# resolutions are the streaming sites' home turf (anivexa); the archive's
# high-quality permanent releases (Nyaa) lead for 1080/original.
_ORDER_LOW = ("anivexa", "nyaa", "hianime")
_ORDER_HIGH = ("nyaa", "anivexa", "hianime")


def order_for(quality: str) -> list[str]:
    """The provider order for one requested resolution.

    An explicit ANIME_PROVIDER_ORDER always wins verbatim. Otherwise 480/720
    try the streaming sidecar first (those resolutions are where it holds
    library and where torrents are scarce), and 1080/original try the torrent
    archive first (the complete permanent source). Nyaa and hianime keep their
    existing behavior in both lists — only their position relative to anivexa
    changes.
    """
    override = os.getenv("ANIME_PROVIDER_ORDER")
    if override:
        return [n.strip() for n in override.split(",") if n.strip()]
    order = _ORDER_LOW if quality in ("480", "720") else _ORDER_HIGH
    return list(order)


class QualityUnavailable(Exception):
    """The episode exists, but not at the requested resolution.

    Distinct from `ProviderError` (the provider itself is unavailable /
    unreachable) so the downloader can try the next provider — and, when no
    provider can serve the requested resolution, fail with a clear
    "requested quality is unavailable" rather than a generic error. Never
    used to mean "download something else instead."
    """


@dataclass
class EpisodeSource:
    """Enough to ask a provider for one episode. Encoded into Track.source_url."""

    provider: str
    anime_id: str  # the provider's own id, not AniList's
    anime_title: str  # AniList best title, for labels and logging
    year: int | None
    season: int
    episode: int
    # The AniList Media id of the *season* this episode belongs to. Carried in
    # the plan URL as a `#anilist=` fragment so a provider keyed by AniList ids
    # (anivexa) can serve an episode even when another provider resolved the
    # plan (Nyaa's anime_id is a title, which anivexa cannot read).
    anilist_id: int | None = None


@dataclass
class ProviderCapability:
    """What one provider is verified to serve for one anime season.

    Filled by an optional `capabilities()` a provider may expose, probed once
    and cached (see anivexa.py). `qualities` is a list of resolutions the
    provider is *verified* to hold ("480"/"720"/...); None means "not probed /
    can't tell" — a consumer must never read None as an absence.
    """

    provider: str
    # "ok" | "unavailable" | "unknown" | "not_suitable"
    status: str = "unknown"
    qualities: list[str] | None = None
    note: str | None = None


@dataclass
class EpisodeStream:
    """A concrete thing the video downloader can pull bytes from."""

    provider: str
    url: str | None = None  # m3u8 master / direct HTTP (scrapers)
    headers: dict = field(default_factory=dict)  # Referer etc. for yt-dlp
    subtitle_url: str | None = None  # optional external .vtt/.srt
    telegram_media: object | None = None  # opaque Telethon media ref
    # Nyaa torrent metadata: the requested episode + whether the magnet is a
    # whole batch we must extract the single episode file from.
    episode: int = 0
    batch: bool = False
    torrent_id: str = ""


class AnimeProvider(Protocol):
    name: str
    # True when episode_stream() returns an HLS/direct URL that
    # anime/downloader.py fetches with yt-dlp. False when download() pulls the
    # bytes itself (Telegram's MTProto path).
    streams_hls: bool = True

    def available(self) -> bool:
        """Credentials/session present? An unavailable provider is skipped."""
        ...

    def resolve(
        self, title: str, year: int | None, anilist_id: int | None = None
    ) -> EpisodeSource:
        """Find this anime on the provider. Raises ProviderError if absent.

        `anilist_id` is the season's AniList Media id — a provider keyed by
        AniList ids (anivexa) needs it, since its `anime_id` *is* the id;
        providers keyed by title (Nyaa, hianime) ignore it.
        """
        ...

    def episode_stream(self, src: EpisodeSource, quality: str) -> EpisodeStream:
        """A concrete stream for one episode, at `quality` (a resolution)."""
        ...

    def episode_count(self, src: EpisodeSource) -> int | None:
        """How many episodes the provider has for this show, or None if unknown.

        Used when AniList's own count is 0 (ongoing series report no final
        count). Only the provider knows what's actually released.
        """
        return None

    def capabilities(self, src: EpisodeSource) -> ProviderCapability | None:
        """Verified per-season capability, or None when the provider has no
        probe (Nyaa/hianime). Statuses: "ok" (verified present), "unavailable"
        (authoritative absence — the downloader may skip without probing),
        "unknown" (couldn't tell — never treated as unavailable), and
        "not_suitable" (present but garbage, e.g. a slideshow-only source).
        """
        return None

    def download(
        self,
        stream: EpisodeStream,
        dest: Path,
        quality: str,
        on_progress: Callable[[float], None],
        should_cancel: Callable[[], bool] | None,
        subs: list[str] | None = None,
    ) -> Path:
        """Fetch the stream's bytes into `dest` (a path without extension).

        `subs` is the list of subtitle languages to mux ("eng"/"fas"); empty or
        None means no subtitles. Returns the video file actually produced. Only
        self-downloading providers (Nyaa torrents) implement this; HLS
        providers leave it to anime/downloader.py.
        """
        raise NotImplementedError  # noqa: PLC0415 — HLS providers don't need it


def providers(order: list[str] | None = None) -> list[AnimeProvider]:
    """The configured providers, in priority order, excluding the unavailable.

    `order` overrides the configured order (used by the downloader to walk the
    chain quality-aware); None uses `order_for`'s high-resolution default.

    Imported lazily inside the function so module import never drags in
    Telethon (a heavy dependency) when only the scraper is configured.
    """
    from . import anivexa
    from . import hianime
    from . import nyaa

    registry: dict[str, type[AnimeProvider]] = {
        "nyaa": nyaa.NyaaProvider,
        "anivexa": anivexa.AnivexaProvider,
        "hianime": hianime.HianimeProvider,
    }
    if order is None:
        order = order_for("1080")
    result: list[AnimeProvider] = []
    for name in order:
        cls = registry.get(name)
        if not cls:
            continue
        try:
            instance = cls()
        except Exception:
            continue  # a broken provider must not kill the chain
        if instance.available():
            result.append(instance)
    return result


def ordered_providers(quality: str) -> list[AnimeProvider]:
    """`providers()` in the quality-aware order for `quality`.

    Tolerates stubs that don't accept the `order` argument (the hermetic tests
    monkeypatch `providers` with no-arg lambdas) by falling back to whatever
    ordering the stub itself uses.
    """
    try:
        return providers(order=order_for(quality))
    except TypeError:
        return providers()
