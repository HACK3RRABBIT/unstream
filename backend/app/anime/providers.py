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
# Nyaa is first: it is the complete, permanent, keyless archive, and the only
# source that works from any network. The others follow as fallbacks.
PROVIDER_ORDER = os.getenv("ANIME_PROVIDER_ORDER", "nyaa,hianime,telegram")


@dataclass
class EpisodeSource:
    """Enough to ask a provider for one episode. Encoded into Track.source_url."""

    provider: str
    anime_id: str  # the provider's own id, not AniList's
    anime_title: str  # AniList best title, for labels and logging
    year: int | None
    season: int
    episode: int


@dataclass
class EpisodeStream:
    """A concrete thing the video downloader can pull bytes from."""

    provider: str
    url: str | None = None  # m3u8 master / direct HTTP (scrapers)
    headers: dict = field(default_factory=dict)  # Referer etc. for yt-dlp
    subtitle_url: str | None = None  # optional external .vtt/.srt
    telegram_media: object | None = None  # opaque Telethon media ref


class AnimeProvider(Protocol):
    name: str
    # True when episode_stream() returns an HLS/direct URL that
    # anime/downloader.py fetches with yt-dlp. False when download() pulls the
    # bytes itself (Telegram's MTProto path).
    streams_hls: bool = True

    def available(self) -> bool:
        """Credentials/session present? An unavailable provider is skipped."""
        ...

    def resolve(self, title: str, year: int | None) -> EpisodeSource:
        """Find this anime on the provider. Raises ProviderError if absent."""
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

    def download(
        self,
        stream: EpisodeStream,
        dest: Path,
        quality: str,
        on_progress: Callable[[float], None],
        should_cancel: Callable[[], bool] | None,
    ) -> Path:
        """Fetch the stream's bytes into `dest` (a path without extension).

        Returns the video file actually produced. Only MTProto providers
        (Telegram) implement this; HLS providers leave it to
        anime/downloader.py.
        """
        raise NotImplementedError  # noqa: PLC0415 — HLS providers don't need it


def providers() -> list[AnimeProvider]:
    """The configured providers, in priority order, excluding the unavailable.

    Imported lazily inside the function so module import never drags in
    Telethon (a heavy dependency) when only the scraper is configured.
    """
    from . import hianime
    from . import nyaa
    from . import telegram  # noqa: PLC0415 — heavy import, keep it lazy

    registry: dict[str, type[AnimeProvider]] = {
        "nyaa": nyaa.NyaaProvider,
        "hianime": hianime.HianimeProvider,
        "telegram": telegram.TelegramProvider,
    }
    order = [name.strip() for name in PROVIDER_ORDER.split(",") if name.strip()]
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
