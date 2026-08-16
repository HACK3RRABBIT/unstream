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
import sqlite3
import threading
import time
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


def translate_dialogue(srt: str, target: str, translator: Translator) -> str:
    """Translate only the dialogue text of SRT content; timestamps and cue
    structure are preserved verbatim. Raises on any translation failure."""
    cues = parse_srt(srt)
    out: list[Cue] = []
    for cue in cues:
        if not cue.text.strip():
            out.append(cue)
            continue
        translated = translator.translate_text(cue.text, target)
        out.append(Cue(index=cue.index, start=cue.start, end=cue.end, text=translated))
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
