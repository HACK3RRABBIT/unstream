"""Persian subtitle translation — keyless Google via translate.py, SQLite-cached.

The cache key is sha256 of the normalized English SRT content plus the target
language, so a changed English subtitle produces a fresh translation and a
re-download of the same episode never re-translates. Translation is backend-only
and a failure never fails the video: `translate_srt_file` returns None and the
caller falls back to the English (or bare) subtitle.

The `Translator` protocol is the seam for future providers (LLM / keyed APIs);
only the existing keyless Google mechanism is implemented today.
"""

import hashlib
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .subtitles import Cue, build_srt, normalize_srt, parse_srt

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "subtitle_translations.db"
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
            _conn.execute(
                "CREATE TABLE IF NOT EXISTS subtitle_translations ("
                "  source_hash TEXT NOT NULL,"
                "  target_language TEXT NOT NULL,"
                "  translated_srt TEXT NOT NULL,"
                "  created_at REAL NOT NULL,"
                "  PRIMARY KEY (source_hash, target_language)"
                ")"
            )
        return _conn


def _cache_get(source_hash: str, target: str) -> str | None:
    try:
        row = _db().execute(
            "SELECT translated_srt FROM subtitle_translations "
            "WHERE source_hash = ? AND target_language = ?",
            (source_hash, target),
        ).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _cache_put(source_hash: str, target: str, translated_srt: str) -> None:
    try:
        _db().execute(
            "INSERT OR REPLACE INTO subtitle_translations "
            "(source_hash, target_language, translated_srt, created_at) "
            "VALUES (?, ?, ?, ?)",
            (source_hash, target, translated_srt, time.time()),
        )
        _db().commit()
    except sqlite3.Error:
        pass  # a cache miss is survivable


class Translator:
    """A subtitle-dialogue translator. `translate_text` returns the translated
    text or raises; callers decide how to degrade."""

    def translate_text(self, text: str, target: str) -> str:
        raise NotImplementedError


class GoogleKeylessTranslator(Translator):
    """The project's existing keyless Google translation, one call per cue."""

    def translate_text(self, text: str, target: str) -> str:
        from .translate import translate_text

        return translate_text(text, target)


_default_translator: Translator | None = None


def get_translator() -> Translator:
    global _default_translator
    if _default_translator is None:
        _default_translator = GoogleKeylessTranslator()
    return _default_translator


# How much cue text to send in one call. The keyless endpoint takes its input
# in the URL, so a chunk has to stay well inside a URL length limit once
# percent-encoded — and a chunk that is refused costs a fallback round trip per
# cue in it, so this stays conservative.
_CHUNK_CHARS = 900

# Chunks in flight at once. An episode is a few dozen chunks of pure waiting;
# six at a time is a large speedup without looking like a scraper to a free
# endpoint that can rate-limit.
_TRANSLATE_WORKERS = max(1, int(os.getenv("SUBTITLE_TRANSLATE_WORKERS", "6") or 6))


def _flatten(text: str) -> str:
    """One cue's dialogue as a single line, so a chunk's lines map 1:1 to cues.

    A cue's own line breaks are layout, not content; the translated track gets
    them back from the player's wrapping. Keeping them would make the line
    count ambiguous and force every cue into its own request.
    """
    return " ".join(text.split())


def _chunk(texts: list[str]) -> list[list[str]]:
    """Group cue texts into request-sized batches, order preserved."""
    chunks: list[list[str]] = []
    current: list[str] = []
    size = 0
    for text in texts:
        if current and size + len(text) + 1 > _CHUNK_CHARS:
            chunks.append(current)
            current, size = [], 0
        current.append(text)
        size += len(text) + 1
    if current:
        chunks.append(current)
    return chunks


def _translate_chunk(
    texts: list[str], target: str, translator: Translator
) -> list[str] | None:
    """Translate a batch of cue texts in one call, or None if it can't be aligned.

    The batch goes out as one newline-separated block and must come back with
    exactly as many lines as it had. Anything else — a merged pair, a dropped
    blank — is a result we cannot map back onto cues, so the caller falls back
    to translating that batch one cue at a time rather than risking a subtitle
    whose lines have slipped against its timings.
    """
    if len(texts) == 1:
        return [translator.translate_text(texts[0], target)]
    translated = translator.translate_text("\n".join(texts), target)
    lines = translated.split("\n")
    if len(lines) != len(texts):
        return None
    return lines


def translate_dialogue(srt: str, target: str, translator: Translator) -> str:
    """Translate only the dialogue text of SRT content; timestamps and cue
    structure are preserved verbatim. Raises on any translation failure.

    An episode is several hundred cues, and asking for them one at a time —
    sequentially — took longer than downloading the video did. Three things
    fix that without touching a single timestamp: identical lines (names,
    "Huh?", sign text) are translated once, the rest travel in batches, and
    the batches go out concurrently. A batch whose answer doesn't line up is
    retried cue by cue, so the timeline can never slip.
    """
    cues = parse_srt(srt)
    unique: list[str] = []
    seen: set[str] = set()
    for cue in cues:
        text = _flatten(cue.text)
        if text and text not in seen:
            seen.add(text)
            unique.append(text)

    chunks = _chunk(unique)
    translations: dict[str, str] = {}
    if chunks:
        workers = min(len(chunks), _TRANSLATE_WORKERS)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(
                pool.map(lambda c: _translate_chunk(c, target, translator), chunks)
            )
        for chunk, result in zip(chunks, results):
            if result is None:
                # Only this batch pays the per-cue price.
                result = [translator.translate_text(text, target) for text in chunk]
            translations.update(zip(chunk, result))

    out: list[Cue] = []
    for cue in cues:
        text = _flatten(cue.text)
        translated = translations.get(text)
        out.append(
            Cue(
                index=cue.index,
                start=cue.start,
                end=cue.end,
                text=translated if translated is not None else cue.text,
            )
        )
    return build_srt(out)


def translate_srt_file(
    source: Path,
    target: str,
    dest: Path,
    translator: Translator | None = None,
) -> Path | None:
    """Translate an English subtitle file to `target`, writing SRT to `dest`.

    Never raises: any failure (parse, network, rate-limit) returns None so the
    video download falls back to the English (or bare) subtitle. Cached by
    sha256(normalized English SRT) + target language.
    """
    translator = translator or get_translator()
    try:
        normalized = normalize_srt(source.read_bytes())
    except Exception:  # noqa: BLE001 — subtitle is nice-to-have
        return None
    if not normalized.strip():
        return None

    source_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    cached = _cache_get(source_hash, target)
    if cached is not None:
        try:
            dest.write_text(cached, encoding="utf-8")
            return dest
        except OSError:
            return None

    try:
        translated = translate_dialogue(normalized, target, translator)
    except Exception:  # noqa: BLE001 — a translation failure never fails a download
        return None
    if not translated.strip():
        return None
    _cache_put(source_hash, target, translated)
    try:
        dest.write_text(translated, encoding="utf-8")
    except OSError:
        return None
    return dest
