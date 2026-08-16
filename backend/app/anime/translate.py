"""Keyless translation for anime descriptions — no account, no API key.

AniList stores one synopsis per anime, essentially always English. When the
UI is Farsi we translate the synopsis on demand through the free Google
Translate endpoint (the same one google.com/translate uses in the browser —
no key, no billing, but a public endpoint that can rate-limit or disappear,
so it is cached and treated as nice-to-have: a translation failure falls back
to the English text rather than failing the card).

The cache lives in SQLite (like analytics and lyrics) so a franchise's
synopses are paid for once, not once per visit.
"""

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

_API = "https://translate.googleapis.com/translate_a/single"
_TIMEOUT = 15
_TTL_SECONDS = 30 * 24 * 60 * 60  # a translation is stable; cache it a month

# A thread per request is enough — anime browsing is not a translation firehose.
_lock = threading.Lock()
_db_path = Path(__file__).resolve().parent.parent.parent / "data" / "anime_translations.db"
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _db_path.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(_db_path, check_same_thread=False)
            _conn.execute(
                "CREATE TABLE IF NOT EXISTS translations ("
                "  key TEXT PRIMARY KEY,"
                "  text TEXT NOT NULL,"
                "  at REAL NOT NULL"
                ")"
            )
        return _conn


def _cache_get(key: str) -> str | None:
    try:
        row = _db().execute(
            "SELECT text, at FROM translations WHERE key = ?", (key,)
        ).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    if time.time() - row[1] > _TTL_SECONDS:
        return None
    return row[0]


def _cache_put(key: str, text: str) -> None:
    try:
        _db().execute(
            "INSERT OR REPLACE INTO translations (key, text, at) VALUES (?, ?, ?)",
            (key, text, time.time()),
        )
        _db().commit()
    except sqlite3.Error:
        pass  # a cache miss is survivable


def _translate(text: str, target: str) -> str:
    """One call to the free endpoint. Raises on network failure."""
    url = f"{_API}?client=gtx&sl=auto&tl={target}&dt=t&q={quote(text)}"
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    parts = [seg[0] for seg in data[0] if seg and seg[0]]
    return "".join(parts)


# A sentence-long synopsis is what we translate; longer blocks risk hitting the
# endpoint's length limits, and a synopsis over a few hundred chars is usually
# an editorial tag-dump worth skipping anyway.
_MAX_CHARS = 1200


def translate_text(text: str, target: str) -> str:
    """One uncached translation call; raises on network failure.

    The public primitive for callers that keep their own cache (the subtitle
    translator). The synopsis `translate()` keeps its per-line cache so browse
    repeats are free; subtitle caching is keyed by subtitle content instead.
    """
    return _translate(text, target)


def translate(text: str, target: str) -> str:
    """Translate `text` to `target`, caching misses. Never raises:
    returns the original text on any failure.

    Only auto-detects/translates when the text looks worth it — the source is
    detected by the endpoint itself (`sl=auto`), so a text that is already in
    the target language comes back unchanged and is cached as-is.
    """
    text = (text or "").strip()
    if not text or len(text) > _MAX_CHARS:
        return text
    key = f"{target}:{text[:200]}"  # cache key bounds the row size
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        out = _translate(text, target)
    except Exception:  # noqa: BLE001 — nice-to-have, never fail a card
        return text
    if out:
        _cache_put(key, out)
    return out or text
