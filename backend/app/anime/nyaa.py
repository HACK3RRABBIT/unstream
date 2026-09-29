"""Nyaa.si torrent provider — the complete, free, permanent anime archive.

Nyaa is the community's archive backbone: every anime, every episode, every
quality, sub/dub/raw, free and keyless since 2007. Its main category is
literally "Anime - English-translated", so English-subbed fansubs land within
hours of an episode airing. It is the one source this project can rely on for
years, and it does not IP-block (verified reachable from this box).

A torrent is not a single stream, so this provider self-downloads: it finds
the best-seeded torrent matching (title, season, episode) on Nyaa, fetches it
with a torrent client (libtorrent when available — the Docker image runs
Python 3.12 which has wheels; aria2c as a fallback), and returns the video
file for the muxer to finalize.

Torrents mean the first download of an episode waits for seeders before bytes
flow — the trade for completeness and permanence.
"""

import re
import shutil
import subprocess
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import logging

from .. import net
from ..downloader import Cancelled, DownloadError
from ..models import ProviderError
from .providers import EpisodeSource, EpisodeStream, QualityUnavailable

log = logging.getLogger("unstream.anime.nyaa")

BASE_URL = "https://nyaa.si"
# The English-translated category filter (f=0 means "no filter", c=1_2 means
# "Anime - English-translated"). We narrow to English subs per the project's
# focus; other sub languages exist under other category ids.
_CATEGORY_ENGLISH = "1_2"

_TIMEOUT = 25
# Routed through net: the desktop's proxy / VPN setting applies here too.
_client = net.http_client(
    headers={
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    },
    timeout=_TIMEOUT,
    follow_redirects=True,
)


# Nyaa searches in flight at once, across the whole process. The per-episode
# quality probe now asks about a whole selection at a time, and nyaa.si is one
# small site: past a handful of parallel searches it starts answering with an
# interstitial, which this code can only read as "couldn't tell". A ceiling
# keeps the concurrency a speedup rather than a way to get blocked.
_SEARCH_SLOTS = threading.BoundedSemaphore(4)


def _search_pages(queries: list[str]) -> list[str]:
    """Fetch several Nyaa searches at once, answers in query order.

    Every lookup asks Nyaa the same question two ways (the SxxExx form and the
    bare episode number), and they don't depend on each other — running them
    one after another doubled the wait before a download could even start. Any
    failure is still a ProviderError: a search that didn't happen must never
    read as a search that found nothing.
    """

    def fetch(query: str) -> str:
        with _SEARCH_SLOTS:
            return _fetch_page(query)

    if len(queries) == 1:
        return [fetch(queries[0])]
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        return list(pool.map(fetch, queries))


def _fetch_page(query: str) -> str:
    """One Nyaa search page, or a ProviderError."""
    try:
        resp = _client.get(
            f"{BASE_URL}/",
            params={
                "f": 0,
                "c": _CATEGORY_ENGLISH,
                "q": query,
                "s": "seeders",
                "o": "desc",
            },
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"Could not reach Nyaa: {exc}") from exc
    return resp.text


# Public UDP trackers as a fallback — some Nyaa swarms only announce here.
_PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://tracker.leechers-paradise.org:6969/announce",
    "udp://open.stealth.si:80/announce",
    "udp://exodus.desync.com:6969/announce",
]

# ── Per-episode availability cache ────────────────────────────────────────────
#
# key = (anime key, season, episode). The resolution set for a released episode
# is stable within a day (Nyaa releases settle within hours), so a non-empty
# verified set lives 15 minutes; an empty set ("nothing seeded right now") is
# re-checked after a minute because seed counts churn and a 0-seeder row can
# turn into 50 seeders before the TTL expires. A probe error is cached as
# "unknown" briefly so a flapping search doesn't hammer itself, but is NEVER
# cached as a verdict. Mirrors the anivexa per-episode cache's shape (LRU,
# single-flight Lock per key, TTL by status, monotonic clock).
_EP_RESOLUTIONS_TTL = 15 * 60
_EP_RESOLUTIONS_EMPTY_TTL = 60
_EP_RESOLUTIONS_UNKNOWN_TTL = 60
_EP_RESOLUTIONS_CACHE_MAX = 500


class _EpResolutionsEntry:
    __slots__ = ("resolutions", "stored_at")

    def __init__(self, resolutions: list[str] | None, stored_at: float):
        # None = unknown (a probe error) — never treated as a verdict.
        self.resolutions = resolutions
        self.stored_at = stored_at


_ep_resolutions_cache: OrderedDict[tuple[str, int, int], _EpResolutionsEntry] = OrderedDict()
_ep_resolutions_locks: dict[tuple[str, int, int], threading.Lock] = {}
_ep_resolutions_guard = threading.Lock()


def _ep_resolutions_ttl_for(resolutions: list[str] | None) -> float:
    if resolutions is None:
        return _EP_RESOLUTIONS_UNKNOWN_TTL  # a probe error — kept briefly
    return _EP_RESOLUTIONS_TTL if resolutions else _EP_RESOLUTIONS_EMPTY_TTL


class _NyaaResolutionsError(ProviderError):
    """A cached probe failure, re-raised so "couldn't ask" never looks like
    "nothing released". The route maps it to an unknown provider status."""


def _ep_resolutions_raise_unknown() -> list[str]:
    raise _NyaaResolutionsError(
        "Could not determine Nyaa resolutions for this episode"
    )


def _cached_ep_resolutions(
    title: str, season: int, episode: int, probe
) -> list[str]:
    """Cached, single-flight wrapper for the Nyaa per-episode resolution probe.

    A probe that fails is cached as *unknown* briefly (60s) so a flapping
    search doesn't hammer itself, but a cached unknown is re-raised — never
    returned as an empty verdict — so the caller (and the route) keeps seeing
    "couldn't tell", never "nothing released". The season is part of the key so
    S01E01 and S03E01 never collide for the same title.
    """
    key = (title, season, episode)

    def _read() -> _EpResolutionsEntry | None:
        with _ep_resolutions_guard:
            return _ep_resolutions_cache.get(key)

    entry = _read()
    if entry and time.monotonic() - entry.stored_at < _ep_resolutions_ttl_for(
        entry.resolutions
    ):
        if entry.resolutions is None:
            return _ep_resolutions_raise_unknown()
        return entry.resolutions
    with _ep_resolutions_guard:
        lock = _ep_resolutions_locks.setdefault(key, threading.Lock())
        owner = lock.acquire(blocking=False)
    if not owner:
        # Another thread is probing this episode; wait for it, then read the
        # cached result rather than issuing a duplicate Nyaa search.
        lock.acquire()
        entry = _read()
        lock.release()
        if entry:
            if entry.resolutions is None:
                return _ep_resolutions_raise_unknown()
            return entry.resolutions
        return _ep_resolutions_raise_unknown()  # probe is still failing
    try:
        resolutions = probe()
    except Exception:
        # A broken search is never a verdict: cache it as unknown (never as an
        # authoritative empty list) and re-raise for the caller.
        with _ep_resolutions_guard:
            _ep_resolutions_cache[key] = _EpResolutionsEntry(None, time.monotonic())
            _ep_resolutions_cache.move_to_end(key)
            while len(_ep_resolutions_cache) > _EP_RESOLUTIONS_CACHE_MAX:
                _ep_resolutions_cache.popitem(last=False)
            lock.release()
            _ep_resolutions_locks.pop(key, None)
        raise
    with _ep_resolutions_guard:
        _ep_resolutions_cache[key] = _EpResolutionsEntry(resolutions, time.monotonic())
        _ep_resolutions_cache.move_to_end(key)
        while len(_ep_resolutions_cache) > _EP_RESOLUTIONS_CACHE_MAX:
            _ep_resolutions_cache.popitem(last=False)
        lock.release()
        _ep_resolutions_locks.pop(key, None)
    return resolutions


# aria2's periodic summary line, e.g.
#   [#7c9e0f 412MiB/1.3GiB(30%) CN:34 SD:6 DL:4.1MiB ETA:3m41s]
# The percentage is the whole download's, which is what the dock wants.
_ARIA2_PROGRESS_RE = re.compile(r"\((\d{1,3})%\)")


def _aria2_progress(log_file: Path) -> float | None:
    """The latest completion fraction aria2 reported, or None if it hasn't yet.

    aria2 has no progress callback and its output goes to a log rather than a
    pipe (an unread pipe fills and blocks the transfer), so the summary line is
    read back from the tail of that log. Only the last one matters, and a log
    that can't be read yet is simply "no news".
    """
    try:
        with log_file.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 8192))
            tail = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    matches = _ARIA2_PROGRESS_RE.findall(tail)
    if not matches:
        return None
    return min(100, int(matches[-1])) / 100


def _magnet(btih: str, name: str) -> str:
    return f"magnet:?xt=urn:btih:{btih}&dn={quote(name)}"


# Subtitle codecs that carry text and can be muxed into mov_text (what the
# mp4 muxer writes). Everything else — dvd_subtitle, hdmv_pgs_subtitle, dvb_subtitle,
# xsub, ... — is a bitmap (picture) subtitle that ffmpeg refuses to re-encode to
# text, so a fansub with only such a track must ship a bare video rather than
# fail the download over a subtitle that cannot be carried.
_TEXT_SUB_CODECS = {
    "subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text",
    "microdvd", "realtext", "subviewer", "subviewer1", "sami", "stl",
}

# A resolution marker in a release title. `p` after 3-4 digits (480p, 1080p)
# is the unambiguous form; dimensions (1280x720, 854×480) are the other. The
# leading `(?<![0-9])` stops "1480p"/"48000" from matching inside a longer
# number, and the trailing `(?![0-9])` on the dimension stops "001-4801".
_RES_TAG = re.compile(r"(?<![0-9])(\d{3,4})p(?!\d)", re.IGNORECASE)
_DIM_TAG = re.compile(r"(?<![0-9])(\d{3,4})\s*[x×]\s*(480|720|1080)(?!\d)", re.IGNORECASE)

# A multi-episode range in a release title: two episode-like numbers joined by
# a dash, en/em dash, tilde, `&` or `+` (1100 & 1101), spaces allowed around the separator (001 ~ 079,
# 001-079, E01–E06). Either endpoint may carry an E/EP/Episode marker. The
# digit boundaries keep it from matching inside a longer number, a width
# (1280x720), or a hash.
_RANGE_RE = re.compile(
    r"(?<!\d)(?:EP|Ep|Episode|E)?\s*(\d{1,4})\s*[-–—~&+]\s*"
    r"(?:EP|Ep|Episode|E)?\s*(\d{1,4})(?!\d)",
    re.IGNORECASE,
)
# An explicit batch label — a multi-episode signal even when the range can't be
# parsed out of the title (Show 001 [BATCH]).
_BATCH_TAG_RE = re.compile(r"\bbatch\b", re.IGNORECASE)

# A multi-episode *list* with no range separator — "Show 001 002 003" or
# "Show E01 E02" — as opposed to a single episode beside a metadata number
# ("Show 01 720p"). Conservative on purpose: only zero-padded episode forms
# (001, 02, 010) and explicit E/EP markers count, and only when two stand
# adjacent (whitespace/comma apart). A year (2001), a resolution (720p), a
# bare number, or an SxxExx marker never does. A missed batch falls through
# to the next provider; a false positive would break a normal single-episode
# download, so it errs toward single.
_MULTI_EP_LIST_RE = re.compile(
    r"(?<!\d)(?:0\d{1,3}|(?:EP|Ep|Episode|E)\s*\d{1,4})(?!\d)"
    r"(?:\s+|\s*,\s*)"
    r"(?<!\d)(?:0\d{1,3}|(?:EP|Ep|Episode|E)\s*\d{1,4})(?!\d)"
)


# ── Reading a release title ───────────────────────────────────────────────────
#
# Release titles carry a lot of numbers that are not episode numbers: a CRC
# ([EDA405E2] — whose "E2" read as episode 2), codecs (x265, H 264), bit depth,
# audio channels (AAC2.0), years, frame rates, and season designators
# ("S2 - 03", "2nd Season", "Season 2"). Matching an episode against the raw
# title picked a Season 2 episode for a Season 1 request and an unrelated show
# for a hash; so a title is first *scrubbed* of that noise, its season read
# out separately, and only then searched for the episode.

_CRC_RE = re.compile(r"[\[(][0-9A-Fa-f]{8}[\])]")
_NOISE_RE = re.compile(
    r"(?<![0-9a-z])(?:"
    r"[xh][ .]?26[45]"  # x264 / H.265 / H 264
    r"|\d{3,4}[pi]"  # 1080p / 1080i
    r"|\d{3,4}\s*[x×]\s*\d{3,4}"  # 1920x1080
    r"|\d{1,2}[- ]?bits?"  # 10bit / 10-Bit
    r"|(?:aac|ddp?|e-?ac-?3|ac-?3|flac|opus|lpcm|dts|truehd|atmos)\s*\d?(?:\.\d)?"
    r"|\d\.\d"  # 2.0 / 5.1
    r"|\d{1,3}(?:\.\d+)?\s*fps"
    r"|(?:19|20)\d{2}\s*[-–]\s*(?:19|20)\d{2}"  # 2011-2012
    r"|[(\[](?:19|20)\d{2}[)\]]"  # (2023)
    r")(?![0-9a-z])",
    re.IGNORECASE,
)
# A season designator, capturing its number: S02E01 (the S02 part), S2 - 03,
# S01 on its own, "2nd Season", "Season 2" / "Season 02".
_SEASON_RE = re.compile(
    r"(?<![0-9a-z])(?:"
    r"s(?:eason)?\s*0*(\d{1,2})(?=e\d|\s*[-–—+]|[\s\]).,_]|$)"
    r"|(\d{1,2})(?:st|nd|rd|th)\s+season"
    r")",
    re.IGNORECASE,
)
_SEASON_SPAN_RE = re.compile(r"(?<![0-9a-z])s0*(\d{1,2})\s*[-–~]\s*s0*(\d{1,2})(?!\d)", re.IGNORECASE)
# Cour/part/volume numbers name a slice of a season, not an episode.
_PART_RE = re.compile(r"(?<![0-9a-z])(?:part|cour|vol\.?|volume)\s*\d{1,2}(?!\d)", re.IGNORECASE)
_DUB_RE = re.compile(r"(?<![a-z])(?:english\s+)?dub(?:bed)?(?![a-z])", re.IGNORECASE)
_DUAL_AUDIO_RE = re.compile(r"(?:dual|multi)[- ]?audio", re.IGNORECASE)


def _scrub(title: str) -> str:
    """The title with hashes and codec/format numbers blanked out."""
    return _NOISE_RE.sub(" ", _CRC_RE.sub(" ", title))


def _declared_seasons(title: str) -> set[int]:
    """The season(s) a title says it belongs to; empty when it says none."""
    clean = _scrub(title)
    seasons = {int(a or b) for a, b in _SEASON_RE.findall(clean)}
    for lo, hi in _SEASON_SPAN_RE.findall(clean):
        lo_i, hi_i = sorted((int(lo), int(hi)))
        seasons.update(range(lo_i, hi_i + 1))
    return seasons


def _episode_text(title: str) -> str:
    """The title with every non-episode number removed — what episode
    markers, ranges and lists are read from."""
    return _PART_RE.sub(" ", _SEASON_RE.sub(" ", _scrub(title)))


def _names_episode(text: str, episode: int) -> tuple[bool, bool]:
    """(does `text` name this episode, does it name it with a marker).

    `text` is already `_episode_text`-cleaned. A marker is E/EP/Episode/#
    standing on its own (so a hash's "5E2" never counts); a bare number counts
    only between delimiters (- 03, (03), 03 [, 03v2).
    """
    marker = re.search(
        rf"(?<![0-9a-z])(?:EP|Episode|E|#)\s*\.?\s*0*{episode}(?:v\d)?(?!\d)",
        text,
        re.IGNORECASE,
    )
    if marker:
        return True, True
    bare = re.search(
        rf"(?:^|[\s\-–—(\[_])0*{episode}(?:v\d)?\s*(?=[\]\s\-–—,)_]|\.mkv|\.mp4|$)",
        text,
    )
    return bool(bare), False


def _tokens(text: str) -> list[str]:
    import unicodedata

    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in folded if not unicodedata.combining(c)).lower()
    folded = re.sub(r"['’`]", "", folded)  # Journey's / Journey`s / Journeys
    return re.findall(r"[a-z0-9]+", folded)


def _core_title(title: str) -> str:
    """A show title without its season/part designator — what a release of any
    season of it still contains."""
    return re.sub(r"\s+", " ", _PART_RE.sub(" ", _SEASON_RE.sub(" ", _scrub(title)))).strip()


def _title_matches(row_title: str, wanted: list[str], full: bool = False) -> bool:
    """Is this release plausibly of one of the `wanted` shows?

    Every word of a wanted title (season designator aside) must appear in the
    release title, or the words run together ("DAN DA DAN" / "Dandadan").
    Nyaa's search is loose — "DAN DA DAN 2" answers with any title containing
    "dan" — so without this an unrelated show could be downloaded. An empty
    `wanted` accepts everything. `full` keeps the season designator in the
    comparison ("Sousou no Frieren 2nd Season" must say "2nd Season").
    """
    if not wanted:
        return True
    row = _tokens(row_title)
    row_set = set(row)
    windows = {
        "".join(row[i:j]) for i in range(len(row)) for j in range(i + 1, min(len(row), i + 6) + 1)
    }
    for title in wanted:
        words = _tokens(title if full else _core_title(title))
        if not words:
            continue
        if set(words) <= row_set or "".join(words) in windows:
            return True
    return False


def effective_season(src_season: int, titles: list[str]) -> int:
    """The season number releases of this entry are labeled with.

    A title that names its own season ("Jujutsu Kaisen 2nd Season") is the
    better witness than the franchise position, which counts split cours as
    seasons; otherwise the franchise position is all there is.
    """
    for title in titles:
        declared = _declared_seasons(title)
        if len(declared) == 1:
            return next(iter(declared))
    return src_season


def _without_titles(text: str, wanted: list[str]) -> str:
    """`text` with the show's own name removed, so the "8" of "Kaiju No. 8"
    or the "100" of "Mob Psycho 100" is never read as an episode number."""
    for title in wanted:
        words = _tokens(_core_title(title))
        if any(w.isdigit() for w in words):
            pattern = r"[\W_]*".join(re.escape(w) for w in words)
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
    return text


def _title_resolution(title: str) -> str | None:
    """The resolution a release title clearly claims, or None.

    Returns "480"/"720"/"1080"/"2160"... (the digits before a `p`), or the
    height half of a width×height tag. Explicitly NOT a loose substring: an
    audio bitrate like "48000" or an episode range like "001-480" carries no
    `p` and is not a resolution. A title with no explicit marker returns
    None, so a caller asking for a specific quality never accepts it.
    """
    m = _RES_TAG.search(title)
    if m:
        return m.group(1)
    m = _DIM_TAG.search(title)
    if m:
        width, height = int(m.group(1)), int(m.group(2))
        # A plausible frame for that height: 16:9 (854/1280/1920) or 4:3
        # (720/960/1440). Guards against junk like a random "1080" digit run.
        if 1.0 <= width / height <= 2.1:
            return m.group(2)
    return None


# Below this many seeders a single-episode release is a weak swarm, and a
# well-seeded batch holding the episode is the faster download.
_WEAK_SWARM = 5


def _best_seeded(candidates: dict[str, dict]) -> dict | None:
    """Pick the best-scored candidate: a true single-episode marker adds 200,
    and an English dub (see `_parse_rows`) ranks below every seeded subbed
    release — it only wins when it is all there is."""
    best = None
    # A dead row (0 seeders) can't be downloaded however well it's named, so
    # it only competes when nothing is alive — otherwise its marker bonus let
    # it outrank a live release and the search reported "nothing seeded".
    live = [t for t in candidates.values() if t["seeders"] > 0]
    for t in live or candidates.values():
        score = (
            t["seeders"]
            + (200 if t.get("explicit") else 0)
            - (100_000 if t.get("dub") else 0)
        )
        if best is None or score > best["score"]:
            best = {**t, "score": score}
    return best


def _episode_ranges(title: str) -> list[tuple[int, int]]:
    """Episode ranges a release title claims, as (start, end) pairs.

    Nyaa batches are titled with the range of episodes they hold
    (001-079, 001 ~ 079, E01–E06), so a requested episode anywhere in that
    range — first, middle or last — belongs to the batch and must be extracted
    from it, never pulled as a whole multi-GiB download. Both endpoints are
    parsed so a mid-range episode whose number never appears in the title is
    still recognized as part of the batch.
    """
    out = []
    for start, end in _RANGE_RE.findall(title):
        lo, hi = int(start), int(end)
        out.append((lo, hi) if lo <= hi else (hi, lo))
    return out


def _multi_episode_space_list(title: str) -> bool:
    """Does `title` list two or more episodes as a space/comma-separated run
    (Show 001 002 003, Show E01 E02) with no range separator?

    Only very strong evidence counts: two adjacent, standalone, zero-padded
    episode numbers (01/001/010) or E/EP markers. A single episode next to a
    metadata number (Show 01 720p, Show 01 1080p) is NOT a batch — the second
    number is a resolution, not an episode — and neither are years or SxxExx
    markers. Conservative on purpose: a missed batch can fall through to the
    next provider, while a false positive would treat a normal single-episode
    release as a batch and break its download.
    """
    return bool(_MULTI_EP_LIST_RE.search(title))


class NyaaProvider:
    name = "nyaa"
    # The provider fetches the torrent itself (torrents aren't an HLS url), so
    # the downloader must call download() rather than yt-dlp on a url.
    streams_hls = False

    def available(self) -> bool:
        # Keyless and free — but a machine with no torrent engine (a desktop
        # build without libtorrent or aria2c) can't fetch anything, and
        # skipping it up front beats failing every episode on it.
        return _has_torrent_client()

    def resolve(
        self, title: str, year: int | None, anilist_id: int | None = None
    ) -> EpisodeSource:
        """Find the anime on Nyaa; the search term is the whole plan."""
        # Nyaa keys everything by search text, so the "id" is the title the
        # per-episode search will use. Season/year are carried through so the
        # per-episode search can disambiguate remakes.
        return EpisodeSource(
            provider=self.name,
            anime_id=title,
            anime_title=title,
            year=year,
            season=0,
            episode=0,
        )

    def _search_episode(self, src: EpisodeSource, episode: int, quality: str = "") -> dict:
        """Return the best-seeded torrent dict for one episode.

        Tries the `S{season}E{episode}` form first when the season is known
        (so "JUJUTSU KAISEN S01E01" isn't matched as S03E01), then falls back
        to the bare episode number for series that Nyaa names by number alone
        (One Piece "1100"). The year is left out (parentheses break Nyaa's
        search).

        For an explicit quality (any height a release claims — 480/720/1080
        and beyond) only torrents whose title clearly claims that resolution
        are eligible; if none exist this raises QualityUnavailable rather than
        silently picking another resolution. `original` (or an empty/unknown
        quality) keeps the best-seeded behavior: torrents carry whatever
        resolution the fansub released.
        """
        title = src.anime_id
        queries, season, titles = self._queries(src, episode)

        wanted = quality if (quality != "original" and quality.isdigit()) else ""

        singles: dict[str, dict] = {}
        batches: dict[str, dict] = {}
        for page in _search_pages(queries):
            page_singles, page_batches = self._parse_rows(page, episode, season, titles)
            for t in page_singles:
                singles.setdefault(t["torrent_id"], t)
            for t in page_batches:
                batches.setdefault(t["torrent_id"], t)

        def healthy(rows: dict[str, dict]) -> bool:
            return any(
                t["seeders"] >= _WEAK_SWARM
                and (not wanted or _title_resolution(t["title"]) == wanted)
                for t in rows.values()
            )

        if not healthy(singles) and not healthy(batches):
            # Nothing well-seeded names the episode. An older show's single-episode
            # releases are often dead while its season packs and batches
            # ("Show S01 [BD 1080p]", "Show (01-25) (Batch)") are seeded — and
            # those never answer an episode-number query. Ask for the show
            # itself and let _parse_rows keep the packs that hold the episode.
            extra = []
            for t in titles[:2]:
                q = _core_title(t)
                if q and q.lower() not in (x.lower() for x in queries + extra):
                    extra.append(q)
            if extra:
                for page in _search_pages(extra):
                    page_singles, page_batches = self._parse_rows(page, episode, season, titles)
                    for t in page_singles:
                        singles.setdefault(t["torrent_id"], t)
                    for t in page_batches:
                        batches.setdefault(t["torrent_id"], t)

        # An explicit request accepts only releases that claim that resolution.
        if wanted:
            singles = {
                k: t
                for k, t in singles.items()
                if _title_resolution(t["title"]) == wanted
            }
            batches = {
                k: t
                for k, t in batches.items()
                if _title_resolution(t["title"]) == wanted
            }

        best = _best_seeded(singles)
        best_batch = _best_seeded(batches)
        # A batch is extracted file-by-file, so it costs the same bytes as a
        # single. It wins when there is no live single — or when the single is
        # barely alive (a couple of seeders can take hours) and the batch has
        # a healthy swarm.
        if best_batch is not None and best_batch["seeders"] > 0 and (
            best is None
            or best["seeders"] == 0
            or (best["seeders"] < _WEAK_SWARM and best_batch["seeders"] >= 3 * best["seeders"])
        ):
            best = {**best_batch, "batch": True}
        if best is None or best["seeders"] == 0:
            if wanted:
                raise QualityUnavailable(
                    f"No {wanted}p release of '{title}' episode {episode} on Nyaa."
                )
            raise ProviderError(
                f"No seeded torrent containing '{title}' episode {episode} found on Nyaa."
            )
        return best

    def episode_resolutions(self, src: EpisodeSource, episode: int) -> list[str]:
        """The resolutions actually released for one episode on Nyaa.

        Runs the same SxxExx + bare-number search as `_search_episode`, then
        unions the explicit resolution marker (`_title_resolution`) across every
        row that names the episode — singles and batches alike, **seeded or
        not**. A 0-seeder release proves the resolution exists (a row can reseed
        tomorrow); it only proves it isn't *obtainable right now*, which is the
        download path's concern, not discovery's. Returns the discovered
        resolutions in release order; [] means **no release at all** names this
        episode yet — the only authoritative-empty verdict.

        Transient states never collapse to []:
          * a timeout, network error or HTTP-200 block page raises ProviderError
            and is cached as *unknown* (qualities=N/A) — never an empty verdict.
          * a query that returns rows but none naming the episode (Nyaa's
            staggered release or a re-order) is a *miss*, not proof the episode
            lacks resolutions — when no query names the episode it raises
            unknown instead of returning [].
        [] is returned only when EVERY query answers a genuine empty search
        page ("No results found") — the provider completed its search and
        found no release by this title at all.

        Cache: a non-empty set lives 15 minutes; a genuinely-empty one is
        re-checked after a minute (releases can appear later); a failed search
        is cached as unknown briefly but still re-raised so the caller can tell
        "couldn't ask" from "nothing released".
        """
        title = src.anime_id
        season = src.season

        queries, label_season, titles = self._queries(src, episode)

        def probe() -> list[str]:

            found: list[str] = []
            emptied: int = 0  # queries that answered a genuine empty search
            inconclusive: int = 0  # queries that were a block page or a miss
            for page in _search_pages(queries):
                kind = self._response_kind(page)
                if kind == "empty":
                    # THE only authoritative-empty signal: Nyaa itself answered
                    # "No results found". This query proves nothing was released
                    # under this form, but says nothing about the other query.
                    emptied += 1
                    continue
                if kind == "block":
                    # An HTTP-200 interstitial/captcha is neither a listing we
                    # can read nor an empty search — inconclusive.
                    inconclusive += 1
                    continue

                singles, batches = self._parse_rows(page, episode, label_season, titles)
                for torrent in [*singles, *batches]:
                    # Seeded or not: a 0-seeder row still proves the resolution
                    # was released for this episode. Only the *download* path
                    # needs live seeders; discovery asks what exists.
                    resolution = _title_resolution(torrent["title"])
                    if resolution and resolution not in found:
                        found.append(resolution)
                if len(singles) + len(batches) == 0:
                    # A real listing whose rows name nothing (Nyaa's release
                    # list is ordered by the `seeders` param, so an episode's
                    # rows may fall below the fold and the search returns only
                    # unrelated releases). A miss is evidence of a partial
                    # search, never that the episode lacks resolutions.
                    inconclusive += 1
            if found:
                return found
            if emptied == len(queries):
                return []  # every query: a real "No results found" search
            if inconclusive:
                # A mix that found nothing but includes a block/miss: the
                # episode's rows may just be reordered. An empty verdict would
                # hide a discoverable episode, so it stays unknown.
                return _ep_resolutions_raise_unknown()
            return _ep_resolutions_raise_unknown()

        return _cached_ep_resolutions(title, season, episode, probe)

    @staticmethod
    def _response_kind(html: str) -> str:
        """Classify a Nyaa search response page: "empty", "list", or "block".

        "empty"   — Nyaa answered "No results found" with no result rows. This
                    is the ONLY authoritative "nothing was released" signal.
        "list"    — a real torrent listing (a torrent-list table with result
                    rows that carry a /view/ link). Rows are parsed further;
                    nothing here implies the episode is absent.
        "block"   — an HTTP-200 response that is neither: an interstitial,
                    captcha or error page. Its rows — if any — are not Nyaa
                    results, so the caller must NOT read it as an empty search.
        """
        if "No results found" in html:
            return "empty"
        # A real listing table; its rows carry /view/ or magnet links. (The
        # class attribute carries extra classes, so match "torrent-list" as a
        # class token, and attachments also register rows.)
        table = re.search(r'<table[^>]*class="[^"]*\btorrent-list\b', html)
        if table and (
            'href="/view/' in html or
            'magnet:' in html or
            'class="attachments"' in html
        ):
            return "list"
        return "block"

    @staticmethod
    def _parse_rows(
        html_text: str,
        episode: int,
        season: int = 0,
        titles: list[str] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Parse a Nyaa search page into (single-episode rows, batch rows).

        Every row naming the episode is returned (not just the best), so the
        caller can filter by resolution before choosing. A row is kept only
        when it is of the wanted show (`titles`, see `_title_matches`), does
        not declare a different season than `season` (0 = don't check), and
        names the episode after the title has been scrubbed of hashes, codec
        numbers and season designators (see `_episode_text`).

        A row is a batch when it holds more than one episode: a range
        (001-574, E01-E06, 001 ~ 079, 1100 & 1101), an explicit `[BATCH]`
        label, a space/comma-separated episode list (001 002 003, E01 E02), or
        a whole-season pack of the requested season (Show S01 [1080p]).
        Batches are kept as an extractable fallback — a single episode is
        always extracted, never the whole multi-GiB torrent.
        """
        import html as html_lib

        wanted = [t for t in (titles or []) if t]
        rows = re.findall(r"<tr[^>]*>.*?</tr>", html_text, re.S)
        singles: list[dict] = []
        batches: list[dict] = []
        for row in rows[1:]:  # first row is the header
            title_m = re.search(r'href="/view/\d+"[^>]*title="([^"]+)"', row)
            magnet = re.search(r'href="(magnet:\?[^"]+)"', row)
            view_m = re.search(r'href="(/view/\d+)"', row)
            seeders_m = re.search(r'class="text-center"[^>]*>(\d+)<', row)
            size_m = re.search(r"([\d.]+\s+(?:GiB|MiB))", row)
            if not title_m or not magnet:
                continue
            title = html_lib.unescape(title_m.group(1))
            if not _title_matches(title, wanted):
                continue
            declared = _declared_seasons(title)
            if season > 0 and declared and season not in declared:
                continue  # another season's release of the same show
            if season > 1 and not declared and not _title_matches(title, wanted, full=True):
                # "Sousou no Frieren - 03" says no season, so it is the first
                # season's — a later season's release names it ("2nd Season",
                # "S2") or carries the later entry's own title.
                continue
            text = _without_titles(_episode_text(title), wanted)
            ranges = _episode_ranges(text)
            named, explicit = _names_episode(text, episode)
            in_range = any(lo <= episode <= hi for lo, hi in ranges)
            batch_tag = bool(_BATCH_TAG_RE.search(title))
            # A season pack names no episode at all ("Show S01 [BD 1080p]",
            # "Show (Season 1) (Batch)"). It holds the episode when it is the
            # requested season's — or, undeclared and tagged a batch, when the
            # request is for the first season.
            season_pack = (
                not named and not ranges
                and ((season > 0 and season in declared)
                     or (batch_tag and not declared and season <= 1))
            )
            if not (named or in_range or season_pack):
                continue
            seeders = int(seeders_m.group(1)) if seeders_m else 0
            common = {
                "title": title,
                "magnet": html_lib.unescape(magnet.group(1)),
                "torrent_id": (view_m.group(1) if view_m else "").rsplit("/", 1)[-1],
                "seeders": seeders,
                "size": size_m.group(1) if size_m else "",
                # An English dub is a different product from the subbed
                # release the subtitle pipeline expects; it only wins when
                # nothing else is seeded.
                "dub": bool(_DUB_RE.search(title)) and not _DUAL_AUDIO_RE.search(title),
            }
            if ranges or batch_tag or season_pack or _multi_episode_space_list(text):
                batches.append(common)
            else:
                singles.append({**common, "explicit": explicit})
        return singles, batches

    @staticmethod
    def _queries(src: EpisodeSource, episode: int) -> tuple[list[str], int, list[str]]:
        """(search queries, the season releases are labeled with, wanted titles).

        Each title the show is known by (the plan's, then AniList's romaji —
        fansub groups mostly name releases in romaji) is asked two ways: the
        SxxExx form under its season-less name, and the zero-padded episode
        number under its full name ("Sousou no Frieren 03" — the unpadded "3"
        matched every release that merely contained the show's name).
        """
        titles: list[str] = []
        for t in (src.anime_id, src.anime_title, *src.alt_titles):
            t = (t or "").strip()
            if t and t.lower() not in (x.lower() for x in titles):
                titles.append(t)
        season = effective_season(src.season, titles)
        queries: list[str] = []
        for t in titles[:2]:
            forms = []
            if season > 0:
                forms.append(f"{_core_title(t)} S{season:02d}E{episode:02d}")
            forms.append(f"{t} {episode:02d}")
            for q in forms:
                if q.lower() not in (x.lower() for x in queries):
                    queries.append(q)
        return queries, season, titles

    def episode_count(self, src: EpisodeSource) -> int | None:
        """Nyaa has no per-show episode registry — the episode number is in
        the torrent title. Unknown until searched, so None (the route then
        needs an explicit episode selection rather than a whole-season batch,
        which Nyaa cannot enumerate).
        """
        return None

    def episode_stream(self, src: EpisodeSource, quality: str) -> EpisodeStream:
        """The best-seeded magnet for `src.episode`, at `quality` when offered.

        When the episode only exists inside a batch, the stream carries the
        batch flag + torrent id so download() can extract just that episode.
        """
        torrent = self._search_episode(src, src.episode, quality)
        return EpisodeStream(
            provider=self.name,
            url=torrent["magnet"],
            headers={},
            episode=src.episode,
            season=effective_season(src.season, [src.anime_id, src.anime_title, *src.alt_titles]),
            batch=torrent.get("batch", False),
            torrent_id=torrent.get("torrent_id", ""),
        )

    def download(
        self,
        stream: EpisodeStream,
        dest: Path,
        quality: str,
        on_progress: Callable[[float], None],
        should_cancel: Callable[[], bool] | None,
        subs: list[str] | None = None,
    ) -> Path:
        """Download the torrent, extract the video, return it as mp4.

        `dest` is a stem (no extension). Anime fansubs embed soft subtitle
        tracks inside the mkv; the requested languages are muxed into the mp4
        (mov_text) so they can be toggled in any player. `subs` is a list of
        "eng"/"fas"; empty/None keeps the video bare. Persian is generated by
        translating the embedded English track when the release has none of its
        own (never fails the download — a failed translation ships English).
        """
        magnet = stream.url
        if not magnet or not magnet.startswith("magnet:"):
            raise DownloadError("Nyaa stream has no magnet link.")

        workdir = dest.parent / f"{dest.name}.nyaatmp"
        workdir.mkdir(parents=True, exist_ok=True)

        client = _pick_torrent_client()
        try:
            # A batch torrent needs the single episode's file selected up front;
            # a single-episode torrent downloads whole.
            if stream.batch and stream.torrent_id:
                video = self._download_batch_episode(
                    client, stream, workdir, on_progress, should_cancel
                )
            else:
                video = self._download_torrent(
                    client, magnet, workdir, on_progress, should_cancel
                )
            if video is None or not video.is_file():
                raise DownloadError("No video file found in the torrent.")

            out = dest.with_name(dest.name + ".mp4")
            if video.suffix.lower() == ".mp4" and not subs:
                video.replace(out)
            else:
                # An mp4 release still goes through the subtitle pass when
                # subtitles were asked for — Persian is generated from its
                # English track like any mkv's.
                self._finalize(video, out, subs or [])
            return out
        except (Cancelled, QualityUnavailable, DownloadError):
            # Being called off is not a failure, and a quality verdict must
            # reach the chain as itself — wrapping either made a cancelled
            # download try the next provider.
            raise
        except Exception as exc:  # noqa: BLE001 — surfaced as a provider failure
            log.warning("Nyaa download failed", exc_info=True)
            raise DownloadError(f"Nyaa download failed: {exc}") from exc
        finally:
            # Clean the torrent working directory either way.
            shutil.rmtree(workdir, ignore_errors=True)

    def _download_batch_episode(self, client: str, stream: EpisodeStream, workdir: Path,
                                on_progress: Callable[[float], None],
                                should_cancel: Callable[[], bool] | None) -> Path | None:
        """Download only the requested episode's file from a batch torrent.

        Fetch the .torrent (Nyaa serves /download/<id>.torrent), list its
        files with aria2 --show-files, find the file whose name carries the
        episode number, and select just that file index so a whole season
        batch doesn't download to extract one episode.
        """
        torrent_url = f"{BASE_URL}/download/{stream.torrent_id}.torrent"
        try:
            resp = _client.get(torrent_url, timeout=_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise DownloadError(f"Could not fetch torrent file: {exc}") from exc

        torrent_file = workdir / f"batch-{stream.torrent_id}.torrent"
        torrent_file.write_bytes(resp.content)

        if client == "aria2c":
            # aria2 lists files as "idx|path|length" lines. (libtorrent reads
            # the file list itself — asking aria2 when it isn't installed was
            # what failed every batch on a machine with only libtorrent.)
            listing = subprocess.run(
                ["aria2c", "--show-files", str(torrent_file)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            if listing.returncode != 0:
                raise DownloadError("Could not list torrent files.")
            target_idx = None
            target_rel = None
            for line in listing.stdout.splitlines():
                # aria2 1.37 prints each file as TWO lines — "idx|path" then
                # "|length" — where the length-only line has an empty index.
                # Only a line whose first field is a numeric file index names
                # a file; the path is validated by _file_is_episode. This also
                # accepts the older single-line "idx|path|length" form.
                parts = line.split("|")
                if (
                    len(parts) >= 2
                    and parts[0].strip().isdigit()
                    and self._file_is_episode(parts[1], stream.episode, stream.season)
                ):
                    target_idx = parts[0].strip()
                    target_rel = self._batch_rel_path(parts[1])
                    break
            if target_idx is None:
                raise DownloadError(
                    f"Episode {stream.episode} not found inside the batch."
                )
            self._aria2_download(
                str(torrent_file), workdir, on_progress, should_cancel,
                select_file=target_idx,
            )
            # Whatever aria2 leaves beside the episode — an unselected file it
            # touched, a placeholder — _largest_video could pick instead of the
            # one we asked for. Return the exact file that --select-file
            # downloaded; never fall back to another episode.
            video = workdir / target_rel
            if not video.is_file():
                raise DownloadError(
                    f"Episode {stream.episode} was not downloaded from the batch."
                )
            return video
        # libtorrent: find the file by name and set priorities.
        return self._libtorrent_batch_download(
            str(torrent_file), workdir, stream.episode, on_progress, should_cancel,
            season=stream.season,
        )

    @staticmethod
    def _file_is_episode(path: str, episode: int, season: int = 0) -> bool:
        """Does a batch file name identify this episode (EP 01 / E01 / - 01)?

        Read from the file's own name (folders are "Show S01" / "Batch" noise)
        with the same scrubbing as release titles, so a CRC is never an
        episode marker. In a multi-season pack a file that declares another
        season is not this one — nor is a file inside another season's folder.
        """
        parts = [p for p in re.split(r"[\\/]", path.strip()) if p]
        if not parts:
            return False
        name = parts[-1]
        if season > 0:
            for piece in (name, *parts[:-1]):
                declared = _declared_seasons(piece)
                if declared and season not in declared:
                    return False
        text = re.sub(r"\.(?:mkv|mp4|avi|webm)$", "", _episode_text(name), flags=re.IGNORECASE)
        if _episode_ranges(text):
            return False  # "01-02.mkv" is a double episode, never extract it as one
        return _names_episode(text + " ", episode)[0]

    @staticmethod
    def _batch_rel_path(raw: str) -> Path:
        """The workdir-relative path of a torrent file, from aria2's listing.

        aria2 prints "./folder/file"; strip the leading "." component and
        refuse anything that could escape the working directory (absolute
        paths, `..` components).
        """
        p = Path(raw.strip())
        if p.parts and p.parts[0] == ".":
            p = Path(*p.parts[1:])
        if p.is_absolute() or ".." in p.parts:
            raise DownloadError("batch file path escapes the working directory")
        return p

    @staticmethod
    def _find_sub_stream(video: Path, language: str) -> str | None:
        """The per-type index of the embedded subtitle stream whose language
        tag matches `language` ("eng"/"fas"), or None.

        ffprobe reports the global stream index, but ffmpeg's `-map 0:s:N`
        wants the per-type (subtitle) index — the line's position among the
        subtitle-only probe output. Using the global index would select the
        wrong subtitle (or none) whenever English isn't the first track.

        Bitmap subtitle streams (dvd_subtitle, hdmv_pgs_subtitle, ...) cannot
        be muxed into mov_text — ffmpeg refuses "subtitle encoding currently
        only possible from text to text or bitmap to bitmap". A track that
        can't be carried is returned as None so the caller falls back to a
        bare video instead of failing the whole download.
        """
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "s",
                "-show_entries", "stream=index,codec_name:stream_tags=language,title",
                "-of", "csv=p=0", str(video),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        for sub_idx, line in enumerate(probe.stdout.strip().splitlines()):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            # parts[0] is the global index, parts[1] the codec, parts[2] the
            # language code; parts[3:] is the title, which a real fansub
            # carries ("2,subrip,eng,English") — join so a code-free track
            # whose title names the language still matches.
            if parts[1].lower() not in _TEXT_SUB_CODECS:
                continue  # bitmap subtitle: cannot become mov_text
            lang = parts[2].lower()
            title = " ".join(parts[3:]).lower()
            if language == "eng" and (
                lang in ("eng", "en") or (not lang and re.search(r"\benglish?\b", title))
            ):
                return str(sub_idx)
            if language == "fas" and (
                lang in ("fas", "fa", "per")
                or (not lang and re.search(r"\bpersian\b|\bfarsi\b", title))
            ):
                return str(sub_idx)
        return None

    @staticmethod
    def _mux_embedded_and_srt(video: Path, out: Path,
                              embedded: list[tuple[str, str]],
                              srt_files: list[tuple[str, Path]]) -> None:
        """Remux a video with a mix of embedded subtitle streams and SRT files
        as soft mov_text tracks, each with its language metadata."""
        from ..downloader import _run_ffmpeg

        args = ["-i", str(video)]
        for _lang, sub in srt_files:
            args += ["-i", str(sub)]
        args += ["-c", "copy", "-map", "0:v", "-map", "0:a?"]
        slot = 0
        for idx, lang in embedded:
            args += ["-map", f"0:s:{idx}", "-c:s", "mov_text",
                     f"-metadata:s:s:{slot}", f"language={lang}"]
            slot += 1
        for k, (lang, _sub) in enumerate(srt_files, start=1):
            args += ["-map", f"{k}:0", "-c:s", "mov_text",
                     f"-metadata:s:s:{slot}", f"language={lang}"]
            slot += 1
        _run_ffmpeg(args, out, "subtitle mux")

    @staticmethod
    def _finalize(video: Path, out: Path, subs: list[str]) -> None:
        """Remux to mp4 with the requested subtitles — and when a subtitle
        can't be muxed, still ship the episode that already downloaded."""
        from ..downloader import _run_ffmpeg

        try:
            NyaaProvider._finalize_with_subs(video, out, subs)
        except DownloadError:
            if not video.exists():
                raise
            log.warning("subtitle mux failed; keeping the bare video", exc_info=True)
            _run_ffmpeg(["-i", str(video), "-map", "0:v", "-map", "0:a?", "-c", "copy"],
                        out, "torrent mux")
            video.unlink(missing_ok=True)

    @staticmethod
    def _finalize_with_subs(video: Path, out: Path, subs: list[str]) -> None:
        """Remux a non-mp4 video to mp4, muxing the requested subtitle tracks.

        `subs` is a list of "eng"/"fas" (empty = none). The tracks are matched
        from the file's embedded stream metadata; when the user asks for Persian
        and the release has no Persian track of its own, one is generated by
        translating the embedded English track. Subtitles are nice-to-have: any
        failure falls back to the English track or a bare video, never failing
        the download.
        """
        from ..downloader import _run_ffmpeg

        if not subs:
            _run_ffmpeg(["-i", str(video), "-c", "copy"], out, "torrent mux")
            video.unlink(missing_ok=True)
            return

        want_eng = "eng" in subs
        want_fas = "fas" in subs
        eng_idx = NyaaProvider._find_sub_stream(video, "eng")
        fas_idx = NyaaProvider._find_sub_stream(video, "fas")

        # Preserve: extract the embedded eng/fas tracks to SRT so a track that
        # didn't match the language heuristic isn't lost. The bare-video mux
        # below only ever runs when the requested languages are genuinely
        # absent, and even then the video itself is still produced.
        from .subtitle_source import extract_embedded

        embedded_srts = extract_embedded(video, out)

        if not want_fas:
            # The single-language path: mux one matching embedded track, or the
            # extracted SRT recovered from the mkv when the heuristic missed
            # it, or a bare video when the language is genuinely absent.
            if want_eng and eng_idx is not None:
                _run_ffmpeg(
                    ["-i", str(video), "-map", "0:v", "-map", "0:a?",
                     "-map", f"0:s:{eng_idx}", "-c", "copy", "-c:s", "mov_text"],
                    out, "subtitle mux",
                )
            elif want_eng and "eng" in embedded_srts:
                _run_ffmpeg(
                    ["-i", str(video), "-i", str(embedded_srts["eng"]),
                     "-map", "0:v", "-map", "0:a?", "-map", "1:0",
                     "-c", "copy", "-c:s", "mov_text",
                     "-metadata:s:s:0", "language=eng"],
                    out, "subtitle mux",
                )
            else:
                _run_ffmpeg(["-i", str(video), "-c", "copy"], out, "torrent mux")
            video.unlink(missing_ok=True)
            return

        # Persian requested. Prefer an embedded Persian track; otherwise
        # translate the embedded English track into an SRT. A failed
        # translation just drops the Persian track.
        fas_track: tuple[str, str | Path] | None = None
        if fas_idx is not None:
            fas_track = ("embedded", fas_idx)
        elif eng_idx is not None:
            eng_srt = video.with_name(video.stem + ".eng.srt")
            try:
                _run_ffmpeg(
                    ["-i", str(video), "-map", f"0:s:{eng_idx}", "-c:s", "srt"],
                    eng_srt, "subtitle extract",
                )
            except Exception:  # noqa: BLE001 — subtitles are nice-to-have
                eng_srt.unlink(missing_ok=True)
                eng_srt = None
            if eng_srt is not None:
                from .subtitle_translate import translate_srt_file

                fas_srt = translate_srt_file(
                    eng_srt, "fa", video.with_name(video.stem + ".fas.srt")
                )
                eng_srt.unlink(missing_ok=True)
                if fas_srt is not None:
                    fas_track = ("srt", fas_srt)
        # No embedded English matched the heuristic; recover it from the mkv's
        # extracted tracks so fas can still be generated from real bytes.
        if fas_track is None and eng_idx is None and "eng" in embedded_srts:
            eng_srt = embedded_srts["eng"]
            from .subtitle_translate import translate_srt_file

            fas_srt = translate_srt_file(
                eng_srt, "fa", video.with_name(video.stem + ".fas.srt")
            )
            if fas_srt is not None:
                fas_track = ("srt", fas_srt)
            embedded_srts.pop("eng", None)

        embedded: list[tuple[str, str]] = []
        srt_files: list[tuple[str, Path]] = []
        if want_eng and eng_idx is not None:
            embedded.append((eng_idx, "eng"))
        elif want_eng and "eng" in embedded_srts:
            srt_files.append(("eng", embedded_srts["eng"]))
        if fas_track is not None:
            if fas_track[0] == "embedded":
                embedded.append((str(fas_track[1]), "fas"))
            else:
                srt_files.append(("fas", fas_track[1]))  # type: ignore[arg-type]

        if not embedded and not srt_files:
            # No subtitles could be produced at all. Fall back to the available
            # English track — a failed translation must never strip the user of
            # subtitles entirely — or a bare video when there's no English.
            if not want_eng and eng_idx is not None:
                embedded.append((eng_idx, "eng"))
            if not embedded:
                _run_ffmpeg(["-i", str(video), "-c", "copy"], out, "torrent mux")
                video.unlink(missing_ok=True)
                return

        NyaaProvider._mux_embedded_and_srt(video, out, embedded, srt_files)
        video.unlink(missing_ok=True)

    def _download_torrent(self, client: str, magnet: str, workdir: Path,
                          on_progress: Callable[[float], None],
                          should_cancel: Callable[[], bool] | None) -> Path | None:
        """Run the torrent client and return the largest video file produced."""
        if client == "aria2c":
            return self._aria2_download(magnet, workdir, on_progress, should_cancel)
        return self._libtorrent_download(magnet, workdir, on_progress, should_cancel)

    def _aria2_download(self, magnet: str, workdir: Path,
                        on_progress: Callable[[float], None],
                        should_cancel: Callable[[], bool] | None,
                        select_file: str | None = None) -> Path | None:
        # DHT + public trackers find peers even when the magnet's own trackers
        # are unreachable — the reason a fresh torrent often stalls at 0%.
        cmd = [
            "aria2c",
            "--dir", str(workdir),
            "--seed-time=0",
            "--enable-dht=true",
            "--bt-tracker-timeout=10",
            # aria2's own cap — long enough for a large episode even on a slow
            # swarm (the job's own progress-aware stall check is stricter).
            "--bt-stop-timeout=3600",
            "--summary-interval=2",
            "--console-log-level=warn",
            "--bt-tracker=" + ",".join(_PUBLIC_TRACKERS),
            # aria2 stops asking for more peers once a torrent exceeds this
            # speed, and the 50 KiB/s default means a well-seeded episode is
            # throttled to a handful of peers for the whole download.
            "--bt-request-peer-speed-limit=50M",
            "--bt-max-peers=200",
            # Preallocating is pure waiting on a batch torrent, where every
            # unselected file would be written out full-size just to be
            # thrown away.
            "--file-allocation=none",
        ]
        if select_file:
            cmd.append(f"--select-file={select_file}")
        cmd.append(magnet)
        # Output goes to a log file, NOT a pipe: aria2 writes summaries every
        # few seconds, and an unread pipe buffer fills in ~30s, which blocks
        # aria2 mid-transfer (a large episode never finishes).
        log_file = workdir / "aria2.log"
        try:
            with log_file.open("wb") as out:
                proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT)
        except OSError as exc:
            raise DownloadError(f"Could not run aria2c: {exc}") from exc

        # A large episode can take 30+ minutes at modest speeds, so there is no
        # stall timeout: the download runs until aria2 exits or the user
        # cancels. Completion is signalled by aria2's exit code, NOT by the
        # absence of the ".aria2" control file — aria2 can leave it after a
        # successful download. Its periodic summary is read out of the log as
        # it goes, so the dock shows a torrent's real progress instead of
        # sitting at zero until the file appears.
        last_reported = -1.0
        while True:
            if should_cancel and should_cancel():
                proc.terminate()
                raise Cancelled()
            if proc.poll() is not None:
                break  # aria2 exited on its own
            fraction = _aria2_progress(log_file)
            if on_progress and fraction is not None and fraction > last_reported:
                last_reported = fraction
                on_progress(fraction)
            time.sleep(2)
        proc.wait(timeout=300)
        if proc.returncode not in (0, -15):
            raise DownloadError("aria2c failed to download the torrent.")
        # aria2 can leave "<name>.aria2" control files after a successful
        # download; they'd make _largest_video skip the complete file, so drop
        # them now that the process has exited 0.
        for ctl in workdir.rglob("*.aria2"):
            ctl.unlink(missing_ok=True)
        return self._largest_video(workdir)

    def _libtorrent_download(self, magnet: str, workdir: Path,
                             on_progress: Callable[[float], None],
                             should_cancel: Callable[[], bool] | None) -> Path | None:
        import libtorrent as lt

        params = lt.parse_magnet_uri(magnet)
        params.save_path = str(workdir)
        # The magnet's own trackers PLUS public fallbacks — some Nyaa swarms
        # only announce on the public UDP trackers. (Assigning the list used
        # to replace the magnet's own trackers, Nyaa's among them.)
        params.trackers = list(params.trackers) + [
            t for t in _PUBLIC_TRACKERS if t not in params.trackers
        ]
        _lt_run(params, on_progress, should_cancel)
        return self._largest_video(workdir)

    def _libtorrent_batch_download(self, torrent_path: str, workdir: Path, episode: int,
                                   on_progress: Callable[[float], None],
                                   should_cancel: Callable[[], bool] | None,
                                   season: int = 0) -> Path | None:
        """Download only the requested episode's file from a batch torrent."""
        import libtorrent as lt

        try:
            info = lt.torrent_info(torrent_path)
        except Exception as exc:  # noqa: BLE001 — a corrupt .torrent
            raise DownloadError(f"Could not read the batch torrent: {exc}") from exc
        # layout() is libtorrent 2.1's name for what files() used to return.
        files = info.layout() if hasattr(info, "layout") else info.files()
        target = None
        for idx in range(files.num_files()):
            if self._file_is_episode(files.file_path(idx), episode, season):
                target = idx
                break
        if target is None:
            raise DownloadError(f"Episode {episode} not found inside the batch.")

        params = lt.add_torrent_params()
        params.ti = info
        params.save_path = str(workdir)
        # Priorities set before the torrent is added, so the unselected files
        # are never created at all.
        params.file_priorities = [
            4 if idx == target else 0 for idx in range(files.num_files())
        ]
        params.trackers = list(_PUBLIC_TRACKERS)
        _lt_run(params, on_progress, should_cancel)
        # The exact file that was selected — never "the largest video", which
        # in a batch can be a neighbour's partial file.
        rel = self._batch_rel_path(files.file_path(target))
        video = workdir / rel
        if not video.is_file():
            raise DownloadError(f"Episode {episode} was not downloaded from the batch.")
        return video

    @staticmethod
    def _largest_video(workdir: Path) -> Path | None:
        # A file still being written has a "<name>.aria2" control file next to
        # it — skipping those avoids remuxing a half-downloaded episode.
        incomplete = {p.with_suffix("") for p in workdir.rglob("*.aria2") if p.is_file()}
        vids = [
            p
            for p in workdir.rglob("*")
            if p.is_file()
            and p not in incomplete
            and p.suffix.lower() in (".mp4", ".mkv", ".avi", ".webm")
        ]
        return max(vids, key=lambda p: p.stat().st_size) if vids else None


# ── libtorrent ────────────────────────────────────────────────────────────────
#
# One session for the whole process. A session per download bound every one of
# them to port 6881 (the second concurrent episode could not listen) and was
# only ever paused, never torn down, so its file handles outlived the download
# — on Windows that made cleaning the working directory fail.
_lt_session = None
_lt_lock = threading.Lock()


def _lt_proxy_settings() -> dict:
    """libtorrent proxy settings for an explicitly configured proxy.

    Only an explicit proxy (Settings → Connection) is applied: the automatic
    mode's system proxy is usually a plain HTTP proxy meant for browsers, and
    forcing every peer connection through it would break torrents that work
    fine directly. SOCKS5 carries trackers, DHT and peers alike.
    """
    from urllib.parse import unquote, urlsplit

    current = net.setting()
    if current in (net.SYSTEM, net.OFF):
        return {"proxy_type": 0}
    parts = urlsplit(current)
    scheme = parts.scheme.lower()
    user = unquote(parts.username) if parts.username else ""
    password = unquote(parts.password) if parts.password else ""
    if scheme.startswith("socks5"):
        kind = 3 if user else 2  # socks5_pw / socks5
    elif scheme.startswith("socks4"):
        kind = 1
    else:
        kind = 5 if user else 4  # http_pw / http
    return {
        "proxy_type": kind,
        "proxy_hostname": parts.hostname or "",
        "proxy_port": parts.port or 0,
        "proxy_username": user,
        "proxy_password": password,
        "proxy_hostnames": True,
        "proxy_peer_connections": True,
        "proxy_tracker_connections": True,
    }


def _lt_get_session():
    import libtorrent as lt

    global _lt_session
    with _lt_lock:
        if _lt_session is None:
            _lt_session = lt.session({
                # Port 0: let the OS pick, so another torrent client on the
                # machine (or a second copy of the app) can't block this one.
                "listen_interfaces": "0.0.0.0:0,[::]:0",
                "enable_dht": True,
                "enable_lsd": True,
                "enable_upnp": True,
                "enable_natpmp": True,
                "connections_limit": 400,
                "alert_mask": 0,
            })
        _lt_session.apply_settings(_lt_proxy_settings())
        return _lt_session


def _lt_run(params, on_progress: Callable[[float], None],
            should_cancel: Callable[[], bool] | None) -> None:
    """Add a torrent to the shared session, drive it to completion (or a
    cancel), and always take it back out so its files are closed."""
    session = _lt_get_session()
    handle = session.add_torrent(params)
    try:
        last = -1.0
        while True:
            if should_cancel and should_cancel():
                raise Cancelled()
            status = handle.status()
            # is_finished: every *wanted* piece is on disk (a batch's
            # unselected files are not wanted).
            if status.is_finished or status.is_seeding:
                break
            if on_progress and status.progress > last + 0.01:
                last = status.progress
                on_progress(status.progress)
            time.sleep(1)
        if on_progress:
            on_progress(1.0)
    finally:
        try:
            session.remove_torrent(handle)
            # Removal closes the files asynchronously; wait briefly so the
            # caller can move or delete them (Windows refuses an open file).
            deadline = time.monotonic() + 10
            while handle.is_valid() and time.monotonic() < deadline:
                time.sleep(0.1)
        except Exception:  # noqa: BLE001 — teardown must not mask the result
            log.debug("libtorrent teardown failed", exc_info=True)


_torrent_client_known: bool | None = None


def _has_torrent_client() -> bool:
    global _torrent_client_known
    if _torrent_client_known is None:
        try:
            _pick_torrent_client()
            _torrent_client_known = True
        except DownloadError:
            _torrent_client_known = False
    return _torrent_client_known or bool(shutil.which("aria2c"))


def _pick_torrent_client() -> str:
    """Prefer aria2c (simplest, no Python-version wheel issues); else libtorrent."""
    if shutil.which("aria2c"):
        return "aria2c"
    try:
        import libtorrent  # noqa: F401

        return "libtorrent"
    except Exception:  # noqa: BLE001 — ImportError, or a native lib that won't load
        raise DownloadError(
            "No torrent client available — install aria2c or the libtorrent Python package."
        )
