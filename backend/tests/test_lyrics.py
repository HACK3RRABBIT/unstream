"""Lyrics: LRC parsing, language detection and the cache in front of LRCLIB.

No network — LRCLIB's responses are faked so the tests pin the logic rather
than the catalog. The cache tests use a throwaway SQLite file, the same way
the sweeper tests swap the downloads directory.
"""

import pytest

from app import lyrics


# --------------------------------------------------------------------------
# LRC -> plain text


def test_strip_lrc_removes_timestamps():
    lrc = "[00:00.15] Is this the real life?\n[00:07.13] Is this just fantasy?\n"
    assert lyrics.strip_lrc(lrc) == "Is this the real life?\nIs this just fantasy?"


def test_strip_lrc_handles_hour_and_hundredths_precision():
    lrc = "[01:02:03.45] Long live the king\n[00:07.1] Round to one decimal\n"
    assert lyrics.strip_lrc(lrc) == "Long live the king\nRound to one decimal"


def test_strip_lrc_drops_metadata_lines():
    lrc = "[ti:Bohemian Rhapsody]\n[ar:Queen]\n[00:00.15] Is this the real life?\n"
    assert lyrics.strip_lrc(lrc) == "Is this the real life?"


def test_strip_lrc_keeps_verse_breaks_but_not_edge_blanks():
    lrc = "[00:00.15] Verse one\n\n[00:07.13] Verse two\n\n"
    assert lyrics.strip_lrc(lrc) == "Verse one\n\nVerse two"


def test_strip_lrc_of_already_plain_text_is_unchanged():
    text = "Already plain\n\nSecond verse"
    assert lyrics.strip_lrc(text) == text


# --------------------------------------------------------------------------
# language detection


def test_detect_lang_farsi():
    assert lyrics.detect_lang("وقتی که تو نیستی") == "fas"


def test_detect_lang_english():
    assert lyrics.detect_lang("Is this the real life?") == "eng"


def test_detect_lang_empty_is_eng():
    assert lyrics.detect_lang("") == "eng"


def test_detect_lang_english_with_a_stray_farsi_word():
    # One Persian word must not flip a mostly-English lyric to `fas`.
    text = "I love you so much " + "دلم" * 1 + " and that's all I know tonight"
    assert lyrics.detect_lang(text) == "eng"


# --------------------------------------------------------------------------
# cache


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(lyrics, "DB_PATH", tmp_path / "lyrics.db")
    lyrics._ensure_schema()
    yield
    lyrics._schema_ready = False


def test_miss_is_cached_and_reused(cache, monkeypatch):
    seen = {"remote": 0}

    def counting(*a, **k):
        seen["remote"] += 1
        return None

    monkeypatch.setattr(lyrics, "_fetch_remote", counting)
    assert lyrics.fetch("Queen", "Bohemian Rhapsody", "A Night at the Opera", 354) is None
    # The second call must be served by the cache, not LRCLIB again.
    assert lyrics.fetch("Queen", "Bohemian Rhapsody", "A Night at the Opera", 354) is None
    assert seen["remote"] == 1


def test_cache_hit_returns_lyrics_without_network(cache, monkeypatch):
    monkeypatch.setattr(
        lyrics, "_fetch_remote", lambda *a, **k: lyrics.Lyrics("hey", "", "lrclib-get")
    )
    assert lyrics.fetch("Queen", "Bohemian Rhapsody", "A Night at the Opera", 354) is not None

    # Cached now: fetch again with a broken remote and it must still answer.
    def boom(*a, **k):
        raise AssertionError("remote should not be reached")

    monkeypatch.setattr(lyrics, "_fetch_remote", boom)
    found = lyrics.fetch("Queen", "Bohemian Rhapsody", "A Night at the Opera", 354)
    assert found is not None and found.plain == "hey"


def test_transient_failure_is_not_cached_as_a_miss(cache, monkeypatch):
    """A network blip must not poison the cache with a week of 'no lyrics'."""

    def failing(*a, **k):
        raise lyrics.LyricsUnavailable("timeout")

    monkeypatch.setattr(lyrics, "_fetch_remote", failing)
    with pytest.raises(lyrics.LyricsUnavailable):
        lyrics.fetch("Queen", "Bohemian Rhapsody", "A Night at the Opera", 354)

    # The miss never landed, so a later successful fetch is still attempted.
    monkeypatch.setattr(
        lyrics, "_fetch_remote", lambda *a, **k: lyrics.Lyrics("hey", "", "lrclib-get")
    )
    assert lyrics.fetch("Queen", "Bohemian Rhapsody", "A Night at the Opera", 354) is not None


def test_cached_miss_expires_after_ttl(cache, monkeypatch):
    monkeypatch.setattr(lyrics, "_fetch_remote", lambda *a, **k: None)
    assert lyrics.fetch("Queen", "Bohemian Rhapsody", "A Night at the Opera", 354) is None

    # Age the miss past the TTL and the next call re-asks the remote.
    monkeypatch.setattr(lyrics, "MISS_TTL_HOURS", 0)
    seen = {"remote": 0}

    def counting(*a, **k):
        seen["remote"] += 1
        return None

    monkeypatch.setattr(lyrics, "_fetch_remote", counting)
    lyrics.fetch("Queen", "Bohemian Rhapsody", "A Night at the Opera", 354)
    assert seen["remote"] == 1


# --------------------------------------------------------------------------
# search candidate picking


def _item(name, artist="Queen", duration=354, plain="lyrics"):
    return {
        "name": name,
        "artistName": artist,
        "duration": duration,
        "plainLyrics": plain,
        "syncedLyrics": "",
    }


def test_pick_prefers_exact_name_and_artist():
    results = [
        _item("Bohemian Rhapsody", "Muppets", 200),
        _item("bohemian rhapsody", "queen", 354),  # exact, but different case
    ]
    found = lyrics._pick(results, "Bohemian Rhapsody", "Queen", 354)
    assert found is not None and found.source == "lrclib-search"


def test_pick_falls_back_to_nearest_duration():
    results = [
        _item("Some Other Song", duration=100),
        _item("Bohemian Rhapsody (Live)", duration=358),
    ]
    found = lyrics._pick(results, "Bohemian Rhapsody", "Queen", 354)
    assert found is not None


def test_pick_rejects_drifted_non_exact_matches():
    results = [_item("Completely Different Song", duration=9999)]
    assert lyrics._pick(results, "Bohemian Rhapsody", "Queen", 354) is None


def test_pick_without_duration_accepts_any_name():
    results = [_item("Long Version Without Duration", duration=0)]
    found = lyrics._pick(results, "Bohemian Rhapsody", "Queen", 0)
    assert found is not None


def test_pick_skips_instrumental_without_lyrics():
    results = [{"name": "Bohemian Rhapsody", "artistName": "Queen", "plainLyrics": "", "syncedLyrics": ""}]
    assert lyrics._pick(results, "Bohemian Rhapsody", "Queen", 354) is None


# --------------------------------------------------------------------------
# Genius hit scoring (the Farsi fallback)


def test_squash_strips_arabic_diacritics():
    """The catalog writes «گل یخ», Genius writes «Gole Yakh - گُلِ یَخ»; the
    comparing must not be fooled by the marks that split the letters."""
    assert lyrics._squash("گل یخ") in lyrics._squash("Gole Yakh - گُلِ یَخ")


def _genius_hit(title, artist, path="/some-song-lyrics", state="complete"):
    return {"result": {"title": title, "primary_artist": {"name": artist}, "path": path, "lyrics_state": state}}


def test_score_genius_hit_persian_substring():
    hit = _genius_hit("Gole Yakh - گُلِ یَخ", "Kourosh Yaghmaei - کورش یغمایی")
    assert lyrics._score_genius_hit(hit, "گل یخ", "کوروش یغمایی") >= 10


def test_score_genius_hit_rejects_unrelated():
    hit = _genius_hit("Something Else Entirely", "Somebody Else")
    assert lyrics._score_genius_hit(hit, "Bohemian Rhapsody", "Queen") < 10


def test_score_genius_hit_ignores_no_lyrics_state():
    hit = _genius_hit("Bohemian Rhapsody", "Queen", state="unreleased")
    # 'unreleased' is accepted; a missing path or absent state is not.
    assert lyrics._score_genius_hit(hit, "Bohemian Rhapsody", "Queen") > 0
    assert lyrics._score_genius_hit(_genius_hit("x", "y", path=""), "x", "y") == 0
    assert lyrics._score_genius_hit(_genius_hit("x", "y", state=""), "x", "y") == 0


def test_find_lyrics_data_walks_nested_state():
    state = {
        "entities": {
            "songs": {"1": {"songPage": {"lyricsData": {"referents": [], "body": {"html": "<p>hey</p>"}}}}}
        }
    }
    block = lyrics._find_lyrics_data(state)
    assert block and block["body"]["html"] == "<p>hey</p>"


def test_genius_header_line_is_stripped():
    text = "[متن آهنگ «گل یخ» از کوروش یغمایی]\n\n[مقدمه]\nغم میون دوتا چشمون قشنگت"
    header = "   [متن آهنگ «گل یخ» از کوروش یغمایی]  ".strip()
    assert lyrics._GENIUS_SONG_HEADER_RE.match(header)
    lines = [ln for ln in text.splitlines() if not lyrics._GENIUS_SONG_HEADER_RE.match(ln.strip())]
    assert "[مقدمه]" in lines and not any("متن آهنگ" in ln for ln in lines)
