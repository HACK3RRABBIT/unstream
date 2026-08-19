"""Anivexa sidecar provider — a streaming-source aggregator behind one HTTP API.

Anivexa-API (a separate Node service, see deploy/anivexa) scrapes ~13 anime
streaming sites and exposes three keyless endpoints the backend talks to:

    /episodes/<provider>/<anilistId>   per-provider episode list (capability)
    /watch/<provider>/<aid>/sub/<prov>-<ep>   per-episode stream + subs
    /map/<anilistId>                   id mappings (imdb/tvdb), for subtitles

This module is a thin facade: it normalizes the sidecar's responses into the
project's EpisodeStream shape, filters out embed/player/slideshow streams,
pre-checks the requested resolution against the master playlist's ladder
(exact quality only — never a silent upgrade), and probes per-season
capabilities (which resolutions the sidecar can actually serve) with a small
single-flight cache.

UNKNOWN vs UNAVAILABLE is load-bearing here: a probe that times out, 5xxes, or
finds the sidecar down is UNKNOWN — never cached as unavailable, never allowed
to block an attempt. Only an authoritative `/episodes` response that lacks the
provider's episodes is UNAVAILABLE, and that is what the downloader may skip.
The sidecar's stream URLs are signed and expiring, so they are never cached —
only status + verified heights are.
"""

import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

import httpx

from ..models import ProviderError
from .providers import (
    EpisodeSource,
    EpisodeStream,
    ProviderCapability,
    QualityUnavailable,
)

# Base URL of the sidecar. Empty disables the provider entirely. Set to the
# sidecar's service name in compose (http://anivexa:4000); a bare local run
# points it at the dev server.
ANIVEXA_URL = os.getenv("ANIVEXA_URL", "").rstrip("/")

# The internal sources the sidecar aggregates, in the order we prefer them for
# each requested resolution. Every one was verified against the live API (see
# docs/PROVIDER-ROUND-3-REPORT.md): anikoto serves 360/1080 HLS with English
# subtitles, anineko + anidbapp serve 360/720/1080 HLS, animedunya serves
# 240/360/480 HLS, and animegg serves direct mp4 (height unlabeled, verified
# by ffprobe after download). The others the sidecar knows (mkissa embed pages,
# reanime 403s, anibd slideshows, ...) are deliberately excluded.
_INTERNAL_CHAIN = {
    "480": ("animedunya", "anineko", "anidbapp", "animegg"),
    "720": ("anineko", "anidbapp", "anikoto", "animegg"),
    "1080": ("anikoto", "anineko", "anidbapp", "animegg"),
    "original": ("animegg", "anikoto", "anineko", "anidbapp"),
}

# Every internal source worth probing (the union of the chains above).
_ALL_INTERNALS = tuple(dict.fromkeys(c for chain in _INTERNAL_CHAIN.values() for c in chain))

_HTTP_TIMEOUT = 10.0
_client = httpx.Client(
    headers={
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "application/json, text/html, */*",
    },
    timeout=_HTTP_TIMEOUT,
    follow_redirects=True,
    # The local proxy env on dev boxes must not eat these calls.
    trust_env=False,
)

# ── Per-season capability cache ───────────────────────────────────────────────
#
# key = (anilist_id, season). A verified aggregate (at least one internal
# provider carries the show, with heights probed from a real master) lives 15
# minutes; an authoritative UNAVAILABLE (the sidecar answered but nothing
# carries it) is re-checked after 2; an UNKNOWN is held briefly so a flapping
# sidecar doesn't hammer itself, but is NEVER treated as unavailable and never
# blocks an attempt. The cache only ever stores status + verified heights, not
# signed/expiring stream URLs.
VERIFIED_TTL = 15 * 60
UNAVAILABLE_TTL = 2 * 60
UNKNOWN_TTL = 60
CACHE_MAX = 500
PROBE_BUDGET = 25.0  # wall-clock seconds for one capability probe

# A dead sidecar is rested (excluded from chain builds) this long after a
# connection-level failure, instead of being re-probed every episode.
SIDECAR_REST = 60


@dataclass
class _CapEntry:
    capability: ProviderCapability
    stored_at: float


_cap_cache: OrderedDict[tuple[int, int], _CapEntry] = OrderedDict()
_cap_locks: dict[tuple[int, int], threading.Lock] = {}
_cap_guard = threading.Lock()
# Episode-aware sibling of `_cap_cache`: keyed (anilist_id, season, episode)
# so per-episode availability is cached and single-flighted the same way the
# season-level probe is — same TTLs by status, same LRU bound, no collisions
# between seasons or episodes. Fast-path: a season authoritatively UNAVAILABLE
# short-circuits without ever probing an episode.
_ep_cache: OrderedDict[tuple[int, int, int], _CapEntry] = OrderedDict()
_ep_locks: dict[tuple[int, int, int], threading.Lock] = {}
_sidecar_rest_until = 0.0


def _rest_sidecar() -> None:
    """Mark the sidecar unreachable so chain builds exclude it briefly."""
    global _sidecar_rest_until
    _sidecar_rest_until = time.monotonic() + SIDECAR_REST


def _get(path: str, timeout: float | None = None) -> httpx.Response:
    """One call to the sidecar. Connection failures rest it, then raise."""
    try:
        resp = _client.get(f"{ANIVEXA_URL}{path}", timeout=timeout)
        resp.raise_for_status()
        return resp
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code >= 500:
            _rest_sidecar()
        raise ProviderError(f"Anivexa HTTP {exc.response.status_code}") from exc
    except Exception as exc:  # noqa: BLE001 — connection/timeout/other
        _rest_sidecar()
        raise ProviderError(f"Could not reach Anivexa: {exc}") from exc


def _internal_status(name: str, anilist_id: int, deadline: float) -> str:
    """Does one internal source carry this anime? ok | unavailable | unknown."""
    remaining = max(1.0, deadline - time.monotonic())
    try:
        resp = _get(f"/episodes/{name}/{anilist_id}", timeout=min(_HTTP_TIMEOUT, remaining))
        data = resp.json()
    except Exception:  # noqa: BLE001 — ProviderError/timeout/JSON all = unknown
        return "unknown"
    if not isinstance(data, dict):
        return "unknown"
    entry = data.get(name)
    if not isinstance(entry, dict) or isinstance(entry.get("error"), str):
        # The sidecar answered but this provider doesn't map the anime, or
        # errored on it — authoritative absence.
        return "unavailable"
    episodes = entry.get("episodes") or {}
    sub = episodes.get("sub") if isinstance(episodes, dict) else None
    return "ok" if sub else "unavailable"


def _master_heights(url: str, headers: dict) -> list[int] | None:
    """The heights a master playlist actually offers, or None if unreadable.

    Returns [] for a playlist that parses but carries no RESOLUTION= lines (a
    single-rendition master — the height can't be verified up front, so the
    caller must fall back to the post-download ffprobe check). None means the
    fetch failed, which must be treated as UNKNOWN, never as a quality absence.
    """
    try:
        resp = httpx.get(
            url,
            headers=headers or {},
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
            trust_env=False,
        )
        resp.raise_for_status()
        text = resp.text
        if not text.lstrip().startswith("#EXTM3U"):
            return None  # a challenge page or HTML, not a playlist
        # A slideshow "video" is a sequence of images, not an episode.
        if re.search(r"\.(?:jpe?g|png|webp)(?:[?#].*)?$", text, re.M):
            return []
        return sorted({int(h) for h in re.findall(r"RESOLUTION=\d+x(\d+)", text)})
    except Exception:  # noqa: BLE001 — unreadable master = unknown, not absent
        return None


def _probe_heights(
    internal: str, anilist_id: int, deadline: float, episode: int = 1
) -> list[int] | None:
    """Verified heights the sidecar can serve for this anime via one internal.

    Fetches `episode`'s watch (default 1, the season-level probe) and reads the
    first direct HLS master's ladder. A direct mp4 (animegg) has no ladder to
    read — None means "can't verify", so it contributes nothing to the verified
    set (the picker treats that resolution as unknown, not absent).
    """
    remaining = max(1.0, deadline - time.monotonic())
    try:
        resp = _get(
            f"/watch/{internal}/{anilist_id}/sub/{internal}-{episode}",
            timeout=min(_HTTP_TIMEOUT, remaining),
        )
        watch = resp.json()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(watch, dict):
        return None
    headers = _watch_headers(watch)
    for stream in _filter_direct(watch):
        if stream["type"] == "hls":
            heights = _master_heights(stream["url"], headers)
            if heights:
                return heights
    return None


def _probe_capability(anilist_id: int, season: int) -> ProviderCapability:
    """One full capability probe for a season, within a 25s wall-clock budget."""
    deadline = time.monotonic() + PROBE_BUDGET
    present: list[str] = []
    seen_unknown = False
    for internal in _ALL_INTERNALS:
        if time.monotonic() > deadline:
            seen_unknown = True
            break
        status = _internal_status(internal, anilist_id, deadline)
        if status == "ok":
            present.append(internal)
        elif status == "unknown":
            seen_unknown = True

    if not present:
        # Nothing carries it: unavailable if the sidecar answered authoritatively
        # for every source, unknown if any probe couldn't get an answer.
        status = "unavailable" if not seen_unknown else "unknown"
        note = None if status == "unavailable" else "probe could not be completed"
        return ProviderCapability("anivexa", status=status, note=note)

    # Verified: collect the union of heights actually read from real masters.
    heights: list[int] = []
    for internal in present:
        if time.monotonic() > deadline:
            break
        got = _probe_heights(internal, anilist_id, deadline)
        if got:
            heights = sorted(set(heights) | set(got))
    return ProviderCapability(
        "anivexa",
        status="ok",
        qualities=[str(h) for h in heights] or None,
        note=f"carried by: {', '.join(sorted(present))}",
    )


def _store(anilist_id: int, season: int, capability: ProviderCapability) -> None:
    with _cap_guard:
        _cap_cache[(anilist_id, season)] = _CapEntry(capability, time.monotonic())
        _cap_cache.move_to_end((anilist_id, season))
        while len(_cap_cache) > CACHE_MAX:
            _cap_cache.popitem(last=False)


def _ttl_for(status: str) -> float:
    return {
        "ok": VERIFIED_TTL,
        "unavailable": UNAVAILABLE_TTL,
        "unknown": UNKNOWN_TTL,
    }.get(status, UNKNOWN_TTL)


def provider_capability(anilist_id: int, season: int) -> ProviderCapability:
    """Cached, single-flight capability probe for one season.

    Never raises and never treats UNKNOWN as UNAVAILABLE: every failure path
    resolves to an UNKNOWN capability so a flaky sidecar can never silence a
    provider that might work. The caller (downloader / /sources) decides what
    to do with it.
    """
    key = (anilist_id, season)
    with _cap_guard:
        entry = _cap_cache.get(key)
        if entry and time.monotonic() - entry.stored_at < _ttl_for(entry.capability.status):
            return entry.capability
        lock = _cap_locks.setdefault(key, threading.Lock())
        owner = lock.acquire(blocking=False)
    if not owner:
        # Another thread is probing this key; wait for it, then read the cache.
        lock.acquire()
        with _cap_guard:
            entry = _cap_cache.get(key)
        lock.release()
        if entry:
            return entry.capability
        return ProviderCapability("anivexa", status="unknown", note="probe in flight")
    try:
        capability = _probe_capability(anilist_id, season)
    except Exception:  # noqa: BLE001 — a broken probe is never a verdict
        capability = ProviderCapability("anivexa", status="unknown", note="probe raised")
    finally:
        _store(anilist_id, season, capability)
        lock.release()
        with _cap_guard:
            _cap_locks.pop(key, None)
    return capability


def _probe_episode(anilist_id: int, season: int, episode: int) -> ProviderCapability:
    """Episode-aware capability: which heights the sidecar serves for ONE episode.

    Reuses the season probe's two stages against episode `N` instead of 1: the
    internal-source check (`_internal_status`) is season-level (does the sidecar
    carry the anime at all), and `_probe_heights` reads that episode's masters
    (`{internal}-{episode}`). A season that is authoritatively UNAVAILABLE is
    short-circuited — no per-episode watch calls burn the budget.
    """
    deadline = time.monotonic() + PROBE_BUDGET
    season_cap = provider_capability(anilist_id, season)
    if season_cap.status == "unavailable":
        return ProviderCapability(
            "anivexa", status="unavailable", note="season authoritatively absent"
        )
    present: list[str] = []
    seen_unknown = False
    for internal in _ALL_INTERNALS:
        if time.monotonic() > deadline:
            seen_unknown = True
            break
        status = _internal_status(internal, anilist_id, deadline)
        if status == "ok":
            present.append(internal)
        elif status == "unknown":
            seen_unknown = True
    if not present:
        status = "unavailable" if not seen_unknown else "unknown"
        note = None if status == "unavailable" else "probe could not be completed"
        return ProviderCapability("anivexa", status=status, note=note)

    heights: list[int] = []
    for internal in present:
        if time.monotonic() > deadline:
            break
        got = _probe_heights(internal, anilist_id, deadline, episode=episode)
        if got:
            heights = sorted(set(heights) | set(got))
    return ProviderCapability(
        "anivexa",
        status="ok",
        qualities=[str(h) for h in heights] or None,
        note=f"carried by: {', '.join(sorted(present))}",
    )


def _ep_store(anilist_id: int, season: int, episode: int, capability: ProviderCapability) -> None:
    with _cap_guard:
        key = (anilist_id, season, episode)
        _ep_cache[key] = _CapEntry(capability, time.monotonic())
        _ep_cache.move_to_end(key)
        while len(_ep_cache) > CACHE_MAX:
            _ep_cache.popitem(last=False)


def episode_capability(anilist_id: int, season: int, episode: int) -> ProviderCapability:
    """Cached, single-flight capability for ONE episode. Never raises.

    Same contract as `provider_capability` — UNKNOWN is never cached as
    UNAVAILABLE, a broken probe degrades to UNKNOWN — but for a single episode,
    keyed separately from the season-level cache so the two never collide.
    """
    key = (anilist_id, season, episode)
    with _cap_guard:
        entry = _ep_cache.get(key)
        if entry and time.monotonic() - entry.stored_at < _ttl_for(entry.capability.status):
            return entry.capability
        lock = _ep_locks.setdefault(key, threading.Lock())
        owner = lock.acquire(blocking=False)
    if not owner:
        lock.acquire()
        with _cap_guard:
            entry = _ep_cache.get(key)
        lock.release()
        if entry:
            return entry.capability
        return ProviderCapability("anivexa", status="unknown", note="probe in flight")
    try:
        capability = _probe_episode(anilist_id, season, episode)
    except Exception:  # noqa: BLE001 — a broken probe is never a verdict
        capability = ProviderCapability("anivexa", status="unknown", note="probe raised")
    finally:
        _ep_store(anilist_id, season, episode, capability)
        lock.release()
        with _cap_guard:
            _ep_locks.pop(key, None)
    return capability


# ── Watch-response normalization ──────────────────────────────────────────────

# Streams that are not a direct video: player/embed/iframe pages, and images.
_BAD_TYPES = {"embed", "iframe", "player", "episode-player"}
_SLIDESHOW_RE = re.compile(r"\.(?:jpe?g|png|webp)(?:[?#].*)?$", re.I)


def _watch_headers(watch: dict) -> dict:
    """Request headers for a watch response: the sidecar's block, else Referer.

    anikoto returns a top-level `headers` object; most others attach `referer`
    to each stream. Build the merged headers the master fetch and yt-dlp want.
    """
    headers = dict(watch.get("headers") or {})
    for stream in watch.get("streams") or []:
        if isinstance(stream, dict) and stream.get("referer"):
            headers.setdefault("Referer", stream["referer"])
    return headers


def _filter_direct(watch: dict) -> list[dict]:
    """Direct video streams from a watch response, embed/player/slideshow out.

    The sidecar lists every server for an episode — HLS masters, direct mp4s,
    and embedded players (which need a browser, or which the site serves as a
    slideshow of images). Only streams that are a real, fetchable video keep:
    type hls|mp4 with an http(s) url, not an embed page, not a JPG.
    """
    out: list[dict] = []
    for stream in watch.get("streams") or []:
        if not isinstance(stream, dict):
            continue
        url = stream.get("url") or ""
        stype = (stream.get("type") or "").lower()
        if stype in _BAD_TYPES:
            continue
        if not url.startswith(("http://", "https://")):
            continue
        if _SLIDESHOW_RE.search(url):
            continue
        if stype not in ("hls", "mp4"):
            # Trust an untyped stream only when the URL says what it is.
            if ".m3u8" in url.lower():
                stype = "hls"
            elif ".mp4" in url.lower():
                stype = "mp4"
            else:
                continue
        out.append({"url": url, "type": stype})
    return out


_PREFERRED_SUB_LANGS = ("English", "English (US)", "Persian", "Farsi")


def _pick_en_subtitle(watch: dict) -> str | None:
    """The first preferred-language subtitle URL the watch offers, else None.

    Sources differ: anikoto returns a top-level `subtitles` array; animedunya
    attaches subtitles to its stream. Any source is checked, English first.
    """
    streams = watch.get("subtitles") or []
    for stream in watch.get("streams") or []:
        if isinstance(stream, dict):
            streams = streams + (stream.get("subtitles") or [])
    for track in streams:
        if not isinstance(track, dict):
            continue
        label = (track.get("label") or track.get("srclang") or track.get("lang") or "").lower()
        if any(pref.lower() in label for pref in _PREFERRED_SUB_LANGS):
            return track.get("url") or track.get("file")
    return None


def _stream_from(
    internal: str, watch: dict, quality: str, episode: int
) -> EpisodeStream:
    """The direct stream from one watch response, at exactly `quality`.

    Explicit resolutions are verified against the master's RESOLUTION= ladder
    before anything is downloaded: a master that parses and lacks the height is
    authoritative absence (QualityUnavailable), so the next source is asked at
    the SAME quality. A master that can't be read is UNKNOWN — the download
    proceeds and the post-download ffprobe check enforces the height instead.
    A direct mp4 has no ladder to read; the same ffprobe check is its gate.
    """
    headers = _watch_headers(watch)
    for stream in _filter_direct(watch):
        if quality == "original" or not quality.isdigit():
            pass
        elif stream["type"] == "hls":
            heights = _master_heights(stream["url"], headers)
            if heights is None:
                pass  # UNKNOWN: master unreadable — proceed, ffprobe enforces
            elif not heights:
                continue  # a slideshow master is not a video, not an episode
            elif int(quality) not in heights:
                continue  # verified absence — ask the next source, same quality
        return EpisodeStream(
            provider="anivexa",
            url=stream["url"],
            headers=headers or None,
            subtitle_url=_pick_en_subtitle(watch),
        )
    raise QualityUnavailable(
        f"No Anivexa source serves episode {episode} at {quality}."
    )


class AnivexaProvider:
    name = "anivexa"
    streams_hls = True  # episode_stream returns an HLS/direct URL yt-dlp fetches

    def available(self) -> bool:
        # Configured, and not inside a dead-sidecar rest window. A sidecar that
        # is merely down is excluded from chain builds for SIDECAR_REST seconds
        # at a time (the capability probe sets the window), then tried again.
        if not ANIVEXA_URL:
            return False
        return time.monotonic() >= _sidecar_rest_until

    def resolve(
        self, title: str, year: int | None, anilist_id: int | None = None
    ) -> EpisodeSource:
        # The sidecar keys everything by AniList id; the anime_id is that id,
        # and the plan carries the real season id in the #anilist= fragment.
        # The downloader prefers the fragment, so this is safe even when the
        # plan was resolved by another provider.
        return EpisodeSource(
            provider=self.name,
            anime_id=str(anilist_id) if anilist_id else "",
            anime_title=title,
            year=year,
            season=0,
            episode=0,
        )

    def episode_count(self, src: EpisodeSource) -> int | None:
        """The highest episode number the sidecar lists for this anime, if any."""
        aid = src.anilist_id or (int(src.anime_id) if src.anime_id.isdigit() else None)
        if not aid:
            return None
        deadline = time.monotonic() + PROBE_BUDGET
        for internal in _INTERNAL_CHAIN["original"]:
            try:
                resp = _get(f"/episodes/{internal}/{aid}", timeout=min(_HTTP_TIMEOUT, deadline - time.monotonic()))
                data = resp.json()
            except Exception:  # noqa: BLE001
                continue
            entry = (data or {}).get(internal) if isinstance(data, dict) else None
            if not isinstance(entry, dict):
                continue
            episodes = entry.get("episodes") or {}
            if isinstance(episodes, dict):
                numbers = [
                    int(e["number"]) for e in episodes.get("sub") or []
                    if isinstance(e, dict) and str(e.get("number", "")).isdigit()
                ]
                if numbers:
                    return max(numbers)
        return None

    def capabilities(self, src: EpisodeSource) -> ProviderCapability | None:
        aid = src.anilist_id or (int(src.anime_id) if src.anime_id.isdigit() else None)
        if not aid:
            return None
        return provider_capability(aid, src.season)

    def episode_stream(self, src: EpisodeSource, quality: str) -> EpisodeStream:
        """Resolve one episode through the sidecar's internal source chain.

        Each internal source is asked at the SAME requested quality; a source
        that can't serve it (verified absent from its master ladder, or the
        anime simply isn't on it) raises QualityUnavailable and the next is
        tried. Only after every source fails does the whole provider give up,
        so the downloader can continue to Nyaa/hianime at the same resolution.
        """
        aid = src.anilist_id or (int(src.anime_id) if src.anime_id.isdigit() else None)
        if not aid:
            raise ProviderError("Anivexa needs the AniList id to resolve an episode.")
        episode = src.episode
        for internal in _INTERNAL_CHAIN.get(quality, _INTERNAL_CHAIN["original"]):
            try:
                watch = _get(f"/watch/{internal}/{aid}/sub/{internal}-{episode}").json()
            except ProviderError:
                continue  # one internal down isn't the whole sidecar down
            if not isinstance(watch, dict):
                continue
            if not _filter_direct(watch):
                # The sidecar carries the episode but only as an embed/slideshow
                # — not a usable video at any quality from this source.
                continue
            try:
                return _stream_from(internal, watch, quality, episode)
            except QualityUnavailable:
                continue
        raise QualityUnavailable(
            f"Anivexa could not serve episode {episode} at {quality}."
        )

    def download(self, *args, **kwargs):  # pragma: no cover — HLS path only
        raise NotImplementedError("anivexa streams download via anime/downloader.py")
