"""Anime API routes — search AniList, browse a franchise, queue a season.

The download endpoint resolves a season to a list of episode tracks (each a
`Track` with media="video" and an `anime://` source_url plan), then hands the
batch to the shared job machinery — the same ZIP / cancel / sweeper path
music downloads use.
"""

import re
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from .. import analytics, jobs, limits
from ..models import ProviderError, Track
from . import anilist, translate
from .downloader import DEFAULT_VIDEO_QUALITY, VIDEO_QUALITIES
from .providers import EpisodeSource, order_for, ordered_providers

router = APIRouter(prefix="/api/anime", tags=["anime"])

SUBTITLE_LANGS = ("eng", "fas")


class AnimeDownloadRequest(BaseModel):
    media_id: int
    season: int
    quality: str = DEFAULT_VIDEO_QUALITY
    # Optional subset of episode_ids to download; None/omitted = the whole season.
    episode_ids: list[str] | None = None
    # Subtitle languages to mux into each episode ("eng"/"fas"); an empty list
    # (or the legacy "none") means no subtitles. Captured at job start like
    # quality. Defaults to English soft subs.
    subs: list[str] = Field(default_factory=lambda: ["eng"])

    @field_validator("subs", mode="before")
    @classmethod
    def _coerce_subs(cls, value: object) -> list[str]:
        # A legacy single string ("eng") is accepted as a one-element list.
        if isinstance(value, str):
            value = [value]
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("subs must be a list of subtitle languages")
        out: list[str] = []
        for lang in value:
            if lang not in SUBTITLE_LANGS + ("none",):
                raise ValueError("subtitle language must be one of: eng, fas, none")
            if lang != "none":
                out.append(lang)
        return out


def _episode_id(media_id: int, season: int, episode: int) -> str:
    return f"{media_id}:s{season}e{episode}"


def _search_result(media: anilist.AniMedia, season_count: int = 1) -> dict:
    """The shape the anime search UI renders — a card of cover + title + facts.

    `format` lets the client split series (TV) from movies cleanly, and
    `season_count` tells a series card how many seasons the franchise has
    before the user opens it. `description` is AniList's summary — English at
    the source (AniList has no per-language descriptions); the client
    translates it when the locale is Farsi.

    `episodes` is the planned total; `available_episodes` is what actually
    exists right now — for a RELEASING show the two differ (12 planned, 6
    aired), so the card can say "airing — 6 of 12 available".
    """
    return {
        "id": media.id,
        "title": media.best_title,
        "format": media.format,
        "episodes": media.episodes,
        "available_episodes": media.available_episodes,
        "year": media.season_year,
        "status": media.status,
        "cover_url": media.cover_url,
        "description": media.description,
        "season_count": season_count,
    }


def _group_franchises(results: list[anilist.AniMedia]) -> tuple[list[dict], list[dict]]:
    """Split search results into (series franchises, movies).

    A franchise is the TV chain: follow each result's SEQUEL/PREQUEL relations
    to its siblings *within the same search page*, group them into one entry
    with a `season_count`, and let the opening card carry the franchise's
    first (earliest) TV entry as its seed — opening it walks the full chain.

    Movies, ONAs and specials are not seasons, so they're returned separately
    and never mixed into a series card.
    """
    tv = [m for m in results if m.format == "TV"]
    movies = [m for m in results if m.format != "TV"]

    by_id = {m.id: m for m in tv}
    # Union-find the TV chain: two entries belong together if one names the
    # other as SEQUEL/PREQUEL.
    parent: dict[int, int] = {m.id: m.id for m in tv}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for media in tv:
        for rtype, related_id, fmt in media.relations:
            if rtype in ("SEQUEL", "PREQUEL") and fmt == "TV" and related_id in by_id:
                a, b = find(media.id), find(related_id)
                if a != b:
                    parent[a] = b

    groups: dict[int, list[anilist.AniMedia]] = {}
    for media in tv:
        root = find(media.id)
        groups.setdefault(root, []).append(media)

    series: list[dict] = []
    for members in groups.values():
        # Earliest first; the seed is the one the user opens.
        members.sort(key=lambda m: (m.season_year is None, m.season_year or 0))
        seed = members[0]
        series.append(_search_result(seed, season_count=len(members)))

    movies_sorted = sorted(
        movies, key=lambda m: (m.season_year is None, m.season_year or 0, m.best_title)
    )
    return series, [_search_result(m) for m in movies_sorted]


def _season_result(media: anilist.AniMedia, season_number: int) -> dict:
    return {
        "season": season_number,
        "media_id": media.id,
        "title": media.best_title,
        "year": media.season_year,
        "episodes": media.episodes,
        "available_episodes": media.available_episodes,
        "status": media.status,
        "cover_url": media.cover_url,
    }


@router.get("/translate")
def anime_translate(
    request: Request,
    text: str = Query(max_length=1300),
    to: str = Query("fa", max_length=8),
) -> dict:
    """Keyless translation of an anime synopsis (AniList is English-only).

    Used by the Farsi UI to show a Persian summary. Cached in SQLite and
    never raises — an unreachable translation endpoint answers with the
    original text rather than failing the card.
    """
    limits.enforce("lyrics", request)  # same shape: a per-card lookup
    if not text.strip():
        raise HTTPException(status_code=400, detail="Empty text")
    if not re.fullmatch(r"[a-z]{2}(-[A-Z]{2})?", to):
        raise HTTPException(status_code=400, detail="Bad target language")
    return {"text": translate.translate(text, to)}


@router.get("/search")
def anime_search(request: Request, q: str = Query(max_length=200)) -> dict:
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Empty search query")
    limits.enforce("search", request)
    try:
        results = anilist.search(q)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    series, movies = _group_franchises(results)
    # The card's season count must match what opening it shows, so walk the
    # full chain (TTL-cached, cheap) rather than counting only what happened
    # to land on this search page.
    for item in series:
        try:
            chain = anilist.franchise(item["id"])
            item["season_count"] = len(chain)
        except ProviderError:
            pass  # keep the page-level count
    analytics.record(
        "anime_search",
        visitor=limits.visitor(request),
        detail="hit" if results else "empty",
        label=" ".join(q.lower().split()),
        value=len(results),
    )
    return {"series": series, "movies": movies}


@router.get("/{media_id}")
def anime_detail(media_id: int, request: Request) -> dict:
    if media_id <= 0:
        raise HTTPException(status_code=400, detail="Bad anime id")
    # One AniList franchise walk — same cost profile as opening an artist.
    limits.enforce("resolve", request)
    try:
        seasons = anilist.franchise(media_id)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not seasons:
        raise HTTPException(status_code=404, detail="Anime not found")

    seed = anilist.get(media_id)
    analytics.record(
        "anime_view",
        visitor=limits.visitor(request),
        source="anilist",
        label=seed.best_title,
        value=len(seasons),
    )
    return {
        "id": media_id,
        "title": seed.best_title,
        "cover_url": seed.cover_url,
        "description": seed.description,
        "seasons": [
            _season_result(media, n) for n, media in enumerate(seasons, start=1)
        ],
    }


@router.post("/download")
def anime_download(body: AnimeDownloadRequest, request: Request) -> dict:
    if body.quality not in VIDEO_QUALITIES:
        raise HTTPException(
            status_code=400,
            detail=f"Quality must be one of: {', '.join(VIDEO_QUALITIES)}",
        )
    client = limits.enforce("download", request)
    if limits.MAX_ACTIVE_JOBS > 0 and jobs.active_count(client) >= limits.MAX_ACTIVE_JOBS:
        raise HTTPException(
            status_code=429,
            detail="Too many downloads at once — wait for one to finish.",
        )

    try:
        seasons = anilist.franchise(body.media_id)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # The franchise is sorted oldest-first; `season` is 1-based into it.
    season_index = body.season - 1
    if season_index < 0 or season_index >= len(seasons):
        raise HTTPException(status_code=400, detail="Bad season number")
    season = seasons[season_index]
    anime_title = seed_best_title(body.media_id, seasons)

    # Provider plan: pick the first provider that resolves this show. One
    # resolve() call per job; each episode then shares the provider's id.
    from ..models import ProviderError as _PE

    plan: EpisodeSource | None = None
    plan_provider = None
    for provider in ordered_providers(body.quality):
        try:
            # Resolve with the *season's* title, so Nyaa can tell Season 1's
            # episode 1 from Season 3's — the franchise seed name alone is
            # ambiguous ("JUJUTSU KAISEN 1" matches S03E01). The season's
            # AniList id is passed for providers keyed by it (anivexa); a
            # provider without that kwarg (a test stub, or a provider that
            # keys by title) is called without it.
            try:
                plan = provider.resolve(
                    season.best_title, season.season_year, anilist_id=season.id
                )
            except TypeError:
                plan = provider.resolve(season.best_title, season.season_year)
            plan_provider = provider
            break
        except _PE:
            continue
    if plan is None or plan_provider is None:
        raise HTTPException(
            status_code=400, detail="No anime provider is available right now."
        )

    # AniList reports 0 episodes for ongoing series; the provider's own list is
    # the source of truth for how many have actually aired. When a provider
    # cannot enumerate a season (Nyaa searches torrent titles, so there is no
    # registry), explicit `episode_ids` still work — we parse the numbers from
    # the ids and find each by title.
    wanted = set(body.episode_ids) if body.episode_ids is not None else None
    if wanted is not None:
        # Build tracks from the explicitly chosen episode numbers.
        wanted_numbers: set[int] = set()
        for key in wanted:
            m = re.match(rf"^{body.media_id}:s{body.season}e(\d+)$", key)
            if m:
                wanted_numbers.add(int(m.group(1)))
        episode_numbers = sorted(wanted_numbers)
    else:
        # The *available* count, not the planned total — an airing season
        # (12 planned, 6 aired) must not queue 12 downloads for 6 episodes.
        episode_count = season.available_episodes
        if episode_count <= 0:
            episode_count = plan_provider.episode_count(plan) or 0
        if episode_count <= 0:
            raise HTTPException(
                status_code=400, detail="This season's episode count is unknown."
            )
        episode_numbers = list(range(1, episode_count + 1))

    # hianime keys a whole show as one entity, so its `season` is 0; the
    # downloader reads the episode number from the URL's last segment.
    season_component = plan.season if plan.season > 0 else body.season
    tracks: list[Track] = []
    for episode in episode_numbers:
        episode_key = _episode_id(body.media_id, body.season, episode)
        tracks.append(
            Track(
                id=episode_key,
                title=f"Episode {episode}",
                artists=[anime_title],
                album=f"{anime_title} — Season {body.season}",
                duration_ms=24 * 60 * 60 * 1000,  # ~24 min; UI/analytics only
                cover_url=season.cover_url,
                track_number=episode,
                media="video",
                subs=body.subs,
                source_url=(
                    f"anime://{plan.provider}/{plan.anime_id}/"
                    f"{season_component}/{episode}#anilist={season.id}"
                ),
            )
        )
    if not tracks:
        raise HTTPException(
            status_code=400, detail="No episodes to download — the selection was empty."
        )

    visitor = limits.visitor(request)
    try:
        job = jobs.start(
            f"{anime_title} — Season {body.season}",
            tracks,
            body.quality,
            embed_lyrics=False,
            owner=client,
            visitor=visitor,
        )
    except jobs.DiskFullError as exc:
        raise HTTPException(status_code=507, detail=str(exc))
    analytics.record(
        "anime_download_start",
        visitor=visitor,
        source=plan.provider,
        detail=body.quality,
        label=anime_title,
        value=len(tracks),
    )
    return {"job_id": job.id}


@router.get("/{media_id}/season/{season}/sources")
def anime_sources(media_id: int, season: int, request: Request) -> dict:
    """Per-provider capability for one season — what the quality picker renders.

    Each configured provider reports its verified status for this season:
      * anivexa — a real (cached, single-flight, 25s-budgeted) capability probe:
        which of its internal sources carry the anime, and the heights verified
        from real master playlists. "unavailable" = the sidecar authoritatively
        lacks the anime; "unknown" = couldn't be completed (never treated as
        absent); "ok" with qualities = verified-servable resolutions.
      * nyaa / hianime — configured and reachable, but their availability is
        per-episode (a torrent title / a search), so `qualities` is null and
        the picker renders those resolutions normally instead of disabling.

    The frontend disables a quality only when every reporting provider
    authoritatively lacks it, so an unknown or null provider never hides an
    option that might work.
    """
    if media_id <= 0 or season <= 0:
        raise HTTPException(status_code=400, detail="Bad anime id or season")
    limits.enforce("resolve", request)
    try:
        seasons = anilist.franchise(media_id)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    season_index = season - 1
    if season_index < 0 or season_index >= len(seasons):
        raise HTTPException(status_code=400, detail="Bad season number")
    season_media = seasons[season_index]

    # One synthetic source per provider, carrying the season's AniList id so a
    # provider keyed by it (anivexa) can probe. Probed concurrently — each
    # probe is bounded by its own 25s budget, so the whole call is too.
    def probe(provider) -> dict:
        name = provider.name
        src = EpisodeSource(
            provider=name,
            anime_id=str(season_media.id),
            anime_title=season_media.best_title,
            year=season_media.season_year,
            season=season,
            episode=1,
            anilist_id=season_media.id,
        )
        capability = getattr(provider, "capabilities", None)
        if capability is None:
            # No probe (Nyaa/hianime): configured and reachable, but per-episode.
            return {
                "name": name,
                "status": "ok" if provider.available() else "unavailable",
                "qualities": None,
                "note": "availability is per-episode (not probed up front)",
            }
        try:
            cap = capability(src)
        except Exception:  # noqa: BLE001 — a broken probe is never a verdict
            cap = None
        if cap is None:
            return {"name": name, "status": "unknown", "qualities": None, "note": None}
        return {
            "name": name,
            "status": cap.status,
            "qualities": cap.qualities,
            "note": cap.note,
        }

    with ThreadPoolExecutor(max_workers=len(order_for("1080"))) as pool:
        results = list(pool.map(probe, ordered_providers("1080")))
    return {"media_id": season_media.id, "season": season, "providers": results}


def seed_best_title(media_id: int, seasons: list[anilist.AniMedia]) -> str:
    """The franchise's display name — the seed media's best title, or the
    first season's if the seed isn't in the TV chain."""
    for media in seasons:
        if media.id == media_id:
            return media.best_title
    return seasons[0].best_title if seasons else ""
