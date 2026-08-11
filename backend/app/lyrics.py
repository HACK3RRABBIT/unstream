"""Fetch lyrics from LRCLIB, falling back to Genius, and cache them on disk.

Both sources are keyless — no account, no API key, nothing that can be
revoked or start charging per call — which is the same constraint every other
metadata provider in this app keeps.

Why two sources: LRCLIB is the better one (structured API, plain *and*
time-synced lyrics) but its Persian songs are keyed by romanized titles, so a
catalog that hands us Persian script can never match them. Genius's internal
search API matches Persian queries directly and its song pages carry the
lyrics, so it is the Farsi fallback. English songs are almost always answered
by LRCLIB and never reach Genius.

A track's lyrics can be looked up twice in the worst case — once for the
on-demand view in the UI and once while tagging its download — and a long
album asks for every track in it. So results and misses go into a small
SQLite file next to analytics.db. Misses are cached too: a track with no
lyrics is unlikely to gain them within the hour, and there is no point paying
a third party twice for the same nothing.
"""

import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

LRCLIB_API = "https://lrclib.net/api"
USER_AGENT = "Unstream/0.0.1 (self-hosted music downloader)"
# Genius's song pages and internal API sit behind a JS app; it answers a real
# browser agent and ignores the bare-library one, so it gets its own.
GENIUS_API = "https://genius.com/api"
GENIUS_PAGE = "https://genius.com"
GENIUS_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 5.0

DB_PATH = Path(
    os.getenv("LYRICS_DB_PATH", Path(__file__).resolve().parent.parent / "data" / "lyrics.db")
)

# How long a cached miss stays trusted before LRCLIB is asked again.
MISS_TTL_HOURS = 24 * 7

# Arabic-script codepoints (Arabic, Persian, Urdu…). Anything mostly in here
# is Arabic-script text and wants a `fas` ID3 language tag on its lyrics.
_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")

# An LRC timestamp: [mm:ss], [mm:ss.xx], or [hh:mm:ss.xx]. Metadata lines
# like [ar:Artist] are not touched — the `:` there is not between two digits.
_LRC_TIME_RE = re.compile(r"\[(?:\d{1,2}:)?\d{1,2}:\d{1,2}(?:\.\d{1,3})?\]")
_LRC_META_RE = re.compile(r"^\[[a-z]+:")

# How far (in seconds) a search hit may drift from the catalog duration before
# it stops being "this song". Looser than the downloader's 20 s because lyrics
# catalogs key on title, and a remastered or live take can run long.
MAX_DURATION_DRIFT = 60


@dataclass
class Lyrics:
    plain: str
    synced: str
    source: str  # "lrclib-get" | "lrclib-search" | "genius"


class LyricsUnavailable(Exception):
    """Network or server failure — transient, so never cached as a miss."""


def strip_lrc(synced: str) -> str:
    """Turn synced LRC into plain text: drop timestamps and metadata lines.

    Blank source lines survive as blank lines, so verse breaks in the LRC
    stay visible in the plain text (LRCLIB separates stanzas with them).
    """
    lines: list[str] = []
    for raw in synced.splitlines():
        if not raw.strip():
            lines.append("")
            continue
        line = _LRC_TIME_RE.sub("", raw).strip()
        if not line or _LRC_META_RE.match(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def detect_lang(text: str) -> str:
    """ISO 639-2 language code for lyrics: 'fas' for Arabic-script, else 'eng'.

    Only meaningful when a whole lyric is mostly one script; the >10% bar
    keeps a stray English line in a Persian song from flipping the tag.
    """
    if not text:
        return "eng"
    letters = sum(1 for c in text if c.isalpha())
    if letters and len(_ARABIC_RE.findall(text)) / letters > 0.1:
        return "fas"
    return "eng"


# Arabic-script combining marks and tatweel: they split a letter in "گُلِ یَخ"
# from the same letter in "گل یخ", and comparing across catalogs must not
# care. Also stripped from the cache key so the two forms share one row.
_ARABIC_MARKS_RE = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")


def _normalize(artist: str, title: str) -> str:
    """Cache key: the pair lowercased, punctuation collapsed."""
    text = f"{artist} - {title}".lower()
    return _squash(text)


def _squash(text: str) -> str:
    """Lowercase and collapse punctuation, keeping Arabic-script letters.

    Used for comparing names across catalogs, where a Persian song appears as
    "گل یخ" in one place and "Gole Yakh - گُلِ یَخ" in another — stripping
    non-ASCII would turn both into empty strings and match anything.
    """
    text = _ARABIC_MARKS_RE.sub("", text)
    return re.sub(r"[^a-z0-9\u0600-\u06ff]+", " ", text.lower()).strip()


# Genius prefixes Persian songs with an auto header like
# [متن آهنگ «گل یخ» از کوروش یغمایی] — bookkeeping, not a lyric.
_GENIUS_SONG_HEADER_RE = re.compile(r"^\[متن آهنگ[^\]]*\]\s*$")


# --------------------------------------------------------------------------
# disk cache


_lock = threading.Lock()
_schema_ready = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lyrics (
    key        TEXT PRIMARY KEY,          -- normalized artist + title
    plain      TEXT NOT NULL DEFAULT '',
    synced     TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT '',
    found      INTEGER NOT NULL DEFAULT 0, -- 0 = cached miss
    fetched_at INTEGER NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _lock:
        if _schema_ready:
            return
        try:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _connect() as conn:
                conn.executescript(_SCHEMA)
            _schema_ready = True
        except sqlite3.Error:
            pass  # an unwritable volume costs us the cache, not the service


# Distinguishes "nothing cached" from "cached as a miss", which is the whole
# point of caching misses: None already means "no lyrics" to callers.
_NOT_CACHED = object()


def _read(key: str):
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT plain, synced, source, found, fetched_at FROM lyrics WHERE key = ?",
                (key,),
            ).fetchone()
    except sqlite3.Error:
        return _NOT_CACHED
    if row is None:
        return _NOT_CACHED
    plain, synced, source, found, fetched_at = row
    if found:
        return Lyrics(plain=plain, synced=synced, source=source)
    if time.time() - fetched_at < MISS_TTL_HOURS * 3600:
        return None
    return _NOT_CACHED


def _write(key: str, found: Lyrics | None) -> None:
    try:
        with _lock, _connect() as conn:
            conn.execute(
                """INSERT INTO lyrics (key, plain, synced, source, found, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                     plain = excluded.plain,
                     synced = excluded.synced,
                     source = excluded.source,
                     found = excluded.found,
                     fetched_at = excluded.fetched_at""",
                (
                    key,
                    found.plain if found else "",
                    found.synced if found else "",
                    found.source if found else "",
                    1 if found else 0,
                    int(time.time()),
                ),
            )
    except sqlite3.Error:
        pass  # same deal: a cache that cannot write must not break anything


# --------------------------------------------------------------------------
# fetching


def _get_json(path: str, params: dict, user_agent: str = USER_AGENT) -> object:
    """GET a JSON endpoint. None when the honest answer is "no lyrics";
    raises LyricsUnavailable for anything that might work on a retry."""
    request = Request(
        f"{path}?{urlencode(params)}",
        headers={"User-Agent": user_agent, "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            return None  # a real miss — LRCLIB knows this song, just no lyrics
        raise LyricsUnavailable(f"{path} answered {exc.code}") from exc
    except (OSError, ValueError) as exc:
        raise LyricsUnavailable(str(exc)) from exc


def _lyrics_from(item: dict, source: str) -> Lyrics | None:
    plain = (item.get("plainLyrics") or "").strip()
    synced = (item.get("syncedLyrics") or "").strip()
    if not plain and not synced:
        return None
    if not plain:
        plain = strip_lrc(synced)
    return Lyrics(plain=plain, synced=synced, source=source)


def _pick(results: list, title: str, artist: str, duration_s: float) -> Lyrics | None:
    """Best hit from a fuzzy search: exact name+artist wins, otherwise the
    closest duration within MAX_DURATION_DRIFT. Exactness dominates so an
    exact title beats a slightly-better-timed wrong cover."""
    wanted_title = _squash(title)
    wanted_artist = _squash(artist)
    best, best_score = None, None
    for item in results:
        lyrics = _lyrics_from(item, "lrclib-search")
        if lyrics is None:
            continue
        exact = _squash(item.get("name") or "") == wanted_title and _squash(
            item.get("artistName") or ""
        ) == wanted_artist
        drift = abs((item.get("duration") or 0) - duration_s) if duration_s else 0
        # Without a duration there is no drift to measure — accept any name.
        if not exact and duration_s and drift > MAX_DURATION_DRIFT:
            continue
        # Exact matches sort by duration proximity; non-exact ones by drift.
        score = (0, drift) if exact else (1, drift)
        if best_score is None or score < best_score:
            best, best_score = lyrics, score
    return best


# --------------------------------------------------------------------------
# Genius — the Farsi fallback. LRCLIB keys Persian songs by romanized titles,
# so it can never match a Persian-script catalog; Genius matches Persian
# queries directly and serves the lyrics from the song page's preloaded state.


def _score_genius_hit(hit: dict, title: str, artist: str) -> int:
    """0–12 score for how well a search hit answers the track.

    The title comparison is a two-way substring check because Persian songs
    show up as "Gole Yakh - گُلِ یَخ" — the catalog's "گل یخ" is a substring
    of it, and the romanized half is a substring of an English catalog entry.
    Artist match breaks ties between covers and translations.
    """
    result = hit.get("result") or {}
    if not result.get("path"):
        return 0
    state = result.get("lyrics_state")
    if state not in ("complete", "visible", "unreleased"):
        return 0
    name = _squash(result.get("title") or "")
    artist_name = _squash((result.get("primary_artist") or {}).get("name") or "")
    wanted = _squash(title)
    wanted_artist = _squash(artist)

    score = 0
    if wanted and name and (wanted == name or wanted in name or name in wanted):
        score += 10
    if wanted_artist and artist_name and (
        wanted_artist == artist_name
        or wanted_artist in artist_name
        or artist_name in wanted_artist
    ):
        score += 6
    return score


def _find_lyrics_data(node) -> dict | None:
    """The first `lyricsData` block anywhere in Genius's preloaded state."""
    if isinstance(node, dict):
        block = node.get("lyricsData")
        if isinstance(block, dict) and block.get("body"):
            return block
        for value in node.values():
            found = _find_lyrics_data(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_lyrics_data(value)
            if found is not None:
                return found
    return None


def _genius_lyrics_from_page(path: str) -> str | None:
    """Pull the lyric text out of a Genius song page. None = no lyrics there."""
    request = Request(
        GENIUS_PAGE + path,
        headers={"User-Agent": GENIUS_USER_AGENT, "Accept": "text/html"},
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", "replace")
    except HTTPError as exc:
        raise LyricsUnavailable(f"genius page answered {exc.code}") from exc
    except OSError as exc:
        raise LyricsUnavailable(str(exc)) from exc

    # The lyrics ride along in `window.__PRELOADED_STATE__ = JSON.parse('...')`
    # — a JS string literal whose content is the state JSON.
    match = re.search(r"window\.__PRELOADED_STATE__ = JSON\.parse\('", html)
    if not match:
        return None
    end = match.end()
    raw: list[str] = []
    i = end
    while i < len(html):
        char = html[i]
        if char == "\\":
            if i + 1 >= len(html):
                break  # a dangling backslash; treat the rest as literal
            raw.append(char)
            raw.append(html[i + 1])
            i += 2
            continue
        if char == "'":
            break
        raw.append(char)
        i += 1
    try:
        # Decode the JS string literal (JS uses \' where JSON would not), then
        # parse the JSON it contained.
        state = json.loads('"' + "".join(raw).replace("\\'", "'") + '"')
        state = json.loads(state)
    except (ValueError, TypeError):
        return None
    block = _find_lyrics_data(state)
    if block is None:
        return None
    text = re.sub(r"<[^>]+>", "", block["body"].get("html") or "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    lines = [line for line in text.splitlines() if not _GENIUS_SONG_HEADER_RE.match(line.strip())]
    return "\n".join(lines).strip() or None


def _fetch_genius(artist: str, title: str) -> Lyrics | None:
    """Search Genius for the track and pull its lyrics from the best hit."""
    data = _get_json(
        GENIUS_API + "/search/multi",
        {"per_page": "5", "q": f"{title} {artist}".strip()},
        user_agent=GENIUS_USER_AGENT,
    )
    hits: list[dict] = []
    if isinstance(data, dict):
        for section in data.get("response", {}).get("sections", []):
            if section.get("type") == "song":
                hits.extend(section.get("hits", []) or [])
    if not hits:
        return None
    # Best first, so a bad page can fall through to the next hit.
    ranked = sorted(
        ((_score_genius_hit(h, title, artist), h) for h in hits),
        key=lambda pair: pair[0],
        reverse=True,
    )
    for score, hit in ranked:
        if score < 10:
            break  # nothing left that plausibly is this song
        path = (hit.get("result") or {}).get("path")
        plain = _genius_lyrics_from_page(path)
        if plain:
            return Lyrics(plain=plain, synced="", source="genius")
    return None


def _fetch_remote(artist: str, title: str, album: str, duration_s: float) -> Lyrics | None:
    # LRCLIB first: structured, synced lyrics, one request. The /get endpoint
    # keys on all four fields and rejects a missing duration, so without one
    # we go straight to its fuzzy search.
    if duration_s:
        exact = _get_json(
            LRCLIB_API + "/get",
            {
                "artist_name": artist,
                "track_name": title,
                "album_name": album,
                "duration": int(duration_s),
            },
        )
        if isinstance(exact, dict):
            found = _lyrics_from(exact, "lrclib-get")
            if found is not None:
                return found

    results = _get_json(
        LRCLIB_API + "/search", {"track_name": title, "artist_name": artist}
    )
    if isinstance(results, list):
        found = _pick(results, title, artist, duration_s)
        if found is not None:
            return found

    # LRCLIB missed (most likely a Persian-script title it keys romanized).
    return _fetch_genius(artist, title)


def fetch(artist: str, title: str, album: str, duration_s: float) -> Lyrics | None:
    """Lyrics for a track, from the cache, LRCLIB, then Genius. None = absent.

    Transient failures raise LyricsUnavailable and are left uncached, so a
    network blip degrades to "no lyrics this time" instead of poisoning the
    cache for a week.
    """
    _ensure_schema()
    key = _normalize(artist, title)
    cached = _read(key)
    if cached is not _NOT_CACHED:
        return cached
    found = _fetch_remote(artist, title, album, duration_s)
    _write(key, found)
    return found
