"""Lyrics: LRC parsing, language detection and the cache in front of LRCLIB.

No network — LRCLIB's responses are faked so the tests pin the logic rather
than the catalog. The cache tests use a throwaway SQLite file, the same way
the sweeper tests swap the downloads directory.
"""

import time

import pytest

from app import lyrics


def _raise_unavailable(*a, **k):
    raise lyrics.LyricsUnavailable("down")


def _boom(*a, **k):
    raise AssertionError("should have been served from cache")


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
    """A network blip must not poison the cache with a week of 'no lyrics'.

    It is cached — as an outage, for minutes — but never as a miss, and a
    caller passing `force` (the retry button) goes back to the sources.
    """
    monkeypatch.setattr(lyrics, "_fetch_remote", _raise_unavailable)
    with pytest.raises(lyrics.LyricsUnavailable):
        lyrics.fetch("Queen", "Bohemian Rhapsody", "A Night at the Opera", 354)

    monkeypatch.setattr(
        lyrics, "_fetch_remote", lambda *a, **k: lyrics.Lyrics("hey", "", "lrclib-get")
    )
    found = lyrics.fetch("Queen", "Bohemian Rhapsody", "A Night at the Opera", 354, force=True)
    assert found is not None and found.plain == "hey"


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


# --------------------------------------------------------------------------
# Persian orthography folding
#
# Every pair below is one song written the way two catalogs actually write it.
# Before the fold, 8 of these 11 split into two cache keys and one of them
# always missed.


@pytest.mark.parametrize(
    "a,b,what",
    [
        ("کي", "کی", "Arabic yeh vs Farsi yeh"),
        ("كورش", "کورش", "Arabic kaf vs keheh"),
        ("بي تو", "بی تو", "both, in a real title"),
        ("می‌خوام", "میخوام", "ZWNJ vs nothing"),
        ("آهنگ", "اهنگ", "alef madda vs bare alef"),
        ("أمير", "امير", "hamza on alef"),
        ("ترانة", "ترانه", "teh marbuta vs heh"),
        ("٧", "۷", "Arabic-Indic vs Persian digits"),
        ("گُلِ یَخ", "گل یخ", "diacritics"),
        ("گــل", "گل", "tatweel"),
    ],
)
def test_squash_folds_persian_spelling_variants(a, b, what):
    assert lyrics._squash(a) == lyrics._squash(b), what


def test_squash_folds_digits_to_ascii():
    assert lyrics._squash("۲۴K Magic") == lyrics._squash("24K Magic")


def test_normalize_gives_one_cache_key_per_song():
    """The fold is what stops one song owning two rows in the cache."""
    assert lyrics._normalize("کوروش يغمايي", "گل يخ") == lyrics._normalize(
        "کوروش یغمایی", "گل یخ"
    )


# --------------------------------------------------------------------------
# Query variants — the shapes our own providers hand us


def test_strip_decorations_removes_feat_and_remaster():
    assert lyrics._strip_decorations("Levitating (feat. DaBaby)") == "Levitating"
    assert lyrics._strip_decorations("Hotel California - 2013 Remaster") == "Hotel California"
    assert lyrics._strip_decorations("Bohemian Rhapsody - Remastered 2011") == "Bohemian Rhapsody"
    assert lyrics._strip_decorations("Blinding Lights (Radio Edit)") == "Blinding Lights"


def test_strip_decorations_keeps_a_leading_parenthetical_title():
    """"(What's the Story) Morning Glory" is the title, not a decoration."""
    title = "(What's the Story) Morning Glory?"
    assert lyrics._strip_decorations(title) == title


def test_strip_decorations_never_empties_a_title():
    assert lyrics._strip_decorations("Live") == "Live"


def test_primary_artist_takes_the_first_credit():
    assert lyrics._primary_artist("Drake, 21 Savage") == "Drake"
    assert lyrics._primary_artist("Calvin Harris & Dua Lipa") == "Calvin Harris"
    assert lyrics._primary_artist("Eagles") == "Eagles"


def test_latin_part_splits_a_mixed_script_field():
    """iTunes returns one field holding both scripts; LRCLIB knows the Latin."""
    assert lyrics._latin_part("Gole Yakh  گل یخ") == "Gole Yakh"


def test_latin_part_is_empty_when_there_is_nothing_to_split():
    assert lyrics._latin_part("Bohemian Rhapsody") == ""
    assert lyrics._latin_part("گل یخ") == ""


def test_variants_are_ordered_deduped_and_capped():
    variants = lyrics._query_variants("Dua Lipa, DaBaby", "Levitating (feat. DaBaby)")
    assert variants[0] == ("Dua Lipa, DaBaby", "Levitating (feat. DaBaby)")
    assert ("Dua Lipa", "Levitating") in variants
    assert len(variants) == len(set(variants)) <= lyrics.MAX_VARIANTS


def test_variants_drop_an_unreadable_artist_only_with_a_duration():
    """Deezer files "Be Khoda" under «محسن چاوشی»; LRCLIB has the title alone.

    Dropping the artist is the only way in, and also the variant most able to
    return a different artist's song of the same name — so the duration that
    verifies it is a precondition, not a bonus.
    """
    args = ("محسن چاوشی", "Be Khoda")
    assert ("", "Be Khoda") in lyrics._query_variants(*args, 214)
    assert ("", "Be Khoda") not in lyrics._query_variants(*args, 0)


def test_variants_keep_the_artist_when_it_is_readable():
    assert ("", "Hello") not in lyrics._query_variants("Adele", "Hello", 295)


# --------------------------------------------------------------------------
# Wrong-match guard


def test_pick_rejects_an_exact_name_at_the_wrong_length():
    """The Live Aid bug: matching the name used to skip the duration check
    entirely, so a 355 s request accepted a 21-minute recording's words."""
    results = [_item("Bohemian Rhapsody", "Queen", 1260)]
    assert lyrics._pick(results, "Bohemian Rhapsody", "Queen", 355) is None


def test_pick_still_allows_a_remaster_to_drift_a_little():
    results = [_item("Bohemian Rhapsody", "Queen", 354 + 30)]
    assert lyrics._pick(results, "Bohemian Rhapsody", "Queen", 354) is not None


# --------------------------------------------------------------------------
# Source fallback: one dead source must not cost us the ones behind it


def test_a_blocked_source_does_not_block_the_next_one(monkeypatch):
    """Genius sits in front of lyrics.ovh and is the one that gets blocked."""
    monkeypatch.setattr(lyrics, "_fetch_lrclib", lambda *a: None)

    def blocked(*a):
        raise lyrics.LyricsUnavailable("403")

    monkeypatch.setattr(lyrics, "_fetch_genius", blocked)
    monkeypatch.setattr(
        lyrics, "_fetch_lyrics_ovh", lambda *a: lyrics.Lyrics("words", "", "lyrics-ovh")
    )
    found = lyrics._fetch_remote("Queen", "Bohemian Rhapsody", "", 354)
    assert found is not None and found.source == "lyrics-ovh"


def test_all_sources_unreachable_is_unavailable_not_absent(monkeypatch):
    """The distinction the whole feature is measured through: "we could not
    ask" must never be cached or reported as "this song has no lyrics"."""

    def blocked(*a):
        raise lyrics.LyricsUnavailable("down")

    monkeypatch.setattr(lyrics, "_fetch_lrclib", blocked)
    monkeypatch.setattr(lyrics, "_fetch_genius", blocked)
    monkeypatch.setattr(lyrics, "_fetch_lyrics_ovh", blocked)
    with pytest.raises(lyrics.LyricsUnavailable):
        lyrics._fetch_remote("Queen", "Bohemian Rhapsody", "", 354)


def test_every_source_answering_no_is_absent(monkeypatch):
    for name in ("_fetch_lrclib", "_fetch_genius", "_fetch_lyrics_ovh"):
        monkeypatch.setattr(lyrics, name, lambda *a: None)
    assert lyrics._fetch_remote("Nobody", "No Such Song", "", 100) is None


def test_lyrics_ovh_rejects_a_stub_answer(monkeypatch):
    """It has no duration to check against, so length is the only signal."""
    monkeypatch.setattr(lyrics, "_get_json", lambda *a, **k: {"lyrics": "Instrumental"})
    assert lyrics._fetch_lyrics_ovh("Queen", "Bohemian Rhapsody") is None


def test_lyrics_ovh_accepts_a_real_answer(monkeypatch):
    monkeypatch.setattr(lyrics, "_get_json", lambda *a, **k: {"lyrics": "la " * 100})
    found = lyrics._fetch_lyrics_ovh("Queen", "Bohemian Rhapsody")
    assert found is not None and found.source == "lyrics-ovh"


# --------------------------------------------------------------------------
# Circuit breaker
#
# A blocked source answers slowly (Genius' 403 measured at ~1.7 s) and a
# fifty-track album used to pay that per track. These pin the "ask once, then
# rest" behaviour and the self-healing that keeps it from needing a restart.


@pytest.fixture(autouse=True)
def _fresh_breakers():
    lyrics.reset_breakers()
    yield
    lyrics.reset_breakers()


def test_breaker_opens_after_repeated_failures():
    for _ in range(lyrics.BREAKER_THRESHOLD):
        assert lyrics._breaker_allows("genius")
        lyrics._breaker_record("genius", ok=False)
    assert not lyrics._breaker_allows("genius")


def test_breaker_stays_shut_for_a_healthy_source():
    for _ in range(lyrics.BREAKER_THRESHOLD * 2):
        lyrics._breaker_record("lrclib", ok=True)
    assert lyrics._breaker_allows("lrclib")


def test_one_success_forgives_earlier_failures():
    lyrics._breaker_record("genius", ok=False)
    lyrics._breaker_record("genius", ok=False)
    lyrics._breaker_record("genius", ok=True)
    for _ in range(lyrics.BREAKER_THRESHOLD - 1):
        lyrics._breaker_record("genius", ok=False)
    assert lyrics._breaker_allows("genius"), "counter should have restarted"


def test_breaker_half_opens_after_the_cooldown(monkeypatch):
    """Recovery needs no restart: one request is let through to find out."""
    for _ in range(lyrics.BREAKER_THRESHOLD):
        lyrics._breaker_record("genius", ok=False)
    assert not lyrics._breaker_allows("genius")
    monkeypatch.setattr(lyrics, "BREAKER_COOLDOWN", 0)
    assert lyrics._breaker_allows("genius")


def test_blocked_source_is_skipped_not_retried(monkeypatch):
    """The point of the whole thing: an album stops paying per track."""
    calls = {"genius": 0}

    def blocked(*a):
        calls["genius"] += 1
        raise lyrics.LyricsUnavailable("403")

    monkeypatch.setattr(lyrics, "_fetch_lrclib", lambda *a: None)
    monkeypatch.setattr(lyrics, "_fetch_genius", blocked)
    monkeypatch.setattr(lyrics, "_fetch_lyrics_ovh", lambda *a: None)

    for _ in range(20):
        with pytest.raises(lyrics.LyricsUnavailable):
            lyrics._fetch_remote("A", "B", "", 100)
    assert calls["genius"] == lyrics.BREAKER_THRESHOLD


def test_skipping_a_source_still_reports_unavailable(monkeypatch):
    """A source we declined to ask might have had the song, so the answer is
    not allowed to harden into "this song has no lyrics"."""
    monkeypatch.setattr(lyrics, "_fetch_lrclib", lambda *a: None)
    monkeypatch.setattr(lyrics, "_fetch_lyrics_ovh", lambda *a: None)
    for _ in range(lyrics.BREAKER_THRESHOLD):
        lyrics._breaker_record("genius", ok=False)
    with pytest.raises(lyrics.LyricsUnavailable):
        lyrics._fetch_remote("A", "B", "", 100)


def test_a_working_source_still_answers_while_another_is_broken(monkeypatch):
    monkeypatch.setattr(
        lyrics, "_fetch_lrclib", lambda *a: lyrics.Lyrics("words", "", "lrclib-get")
    )
    for _ in range(lyrics.BREAKER_THRESHOLD):
        lyrics._breaker_record("genius", ok=False)
    found = lyrics._fetch_remote("A", "B", "", 100)
    assert found is not None and found.source == "lrclib-get"


# --------------------------------------------------------------------------
# Caching the unavailable state


def test_unavailable_is_cached_so_an_album_pays_once(cache, monkeypatch):
    calls = {"n": 0}

    def failing(*a):
        calls["n"] += 1
        raise lyrics.LyricsUnavailable("down")

    monkeypatch.setattr(lyrics, "_fetch_remote", failing)
    for _ in range(10):
        with pytest.raises(lyrics.LyricsUnavailable):
            lyrics.fetch("Queen", "Bohemian Rhapsody", "", 354)
    assert calls["n"] == 1


def test_cached_unavailable_expires_quickly(cache, monkeypatch):
    """Minutes, not the week a real miss gets — a source coming back must be
    noticed almost immediately."""
    monkeypatch.setattr(lyrics, "_fetch_remote", _raise_unavailable)
    with pytest.raises(lyrics.LyricsUnavailable):
        lyrics.fetch("Queen", "Bohemian Rhapsody", "", 354)

    monkeypatch.setattr(lyrics, "UNAVAILABLE_TTL_MINUTES", 0)
    monkeypatch.setattr(
        lyrics, "_fetch_remote", lambda *a: lyrics.Lyrics("back", "", "genius")
    )
    found = lyrics.fetch("Queen", "Bohemian Rhapsody", "", 354)
    assert found is not None and found.plain == "back"


def test_unavailable_does_not_become_a_miss(cache, monkeypatch):
    """The whole distinction: an outage must never be served as "no lyrics"."""
    monkeypatch.setattr(lyrics, "_fetch_remote", _raise_unavailable)
    with pytest.raises(lyrics.LyricsUnavailable):
        lyrics.fetch("Queen", "Bohemian Rhapsody", "", 354)
    with pytest.raises(lyrics.LyricsUnavailable):
        lyrics.fetch("Queen", "Bohemian Rhapsody", "", 354)


def test_a_real_miss_still_outlives_an_outage(cache, monkeypatch):
    """Miss and unavailable are on deliberately different clocks."""
    monkeypatch.setattr(lyrics, "_fetch_remote", lambda *a: None)
    assert lyrics.fetch("Nobody", "No Such Song", "", 100) is None
    monkeypatch.setattr(lyrics, "UNAVAILABLE_TTL_MINUTES", 0)
    monkeypatch.setattr(lyrics, "_fetch_remote", _boom)
    assert lyrics.fetch("Nobody", "No Such Song", "", 100) is None


def test_rows_written_by_the_old_schema_still_read(cache):
    """The three states reuse the existing INTEGER column precisely so this
    ships without a migration. Rows already on disk say 1 and 0."""
    lyrics._ensure_schema()
    with lyrics._connect() as conn:
        conn.execute(
            "INSERT INTO lyrics (key, plain, synced, source, found, fetched_at)"
            " VALUES (?,?,?,?,?,?)",
            ("old - hit", "words", "", "genius", 1, int(time.time())),
        )
        conn.execute(
            "INSERT INTO lyrics (key, plain, synced, source, found, fetched_at)"
            " VALUES (?,?,?,?,?,?)",
            ("old - miss", "", "", "", 0, int(time.time())),
        )
    hit = lyrics._read("old - hit")
    assert isinstance(hit, lyrics.Lyrics) and hit.plain == "words"
    assert lyrics._read("old - miss") is None


def test_force_bypasses_a_cached_outage_but_not_a_hit(cache, monkeypatch):
    """The retry button must reach the sources; nothing else should re-pay."""
    monkeypatch.setattr(lyrics, "_fetch_remote", _raise_unavailable)
    with pytest.raises(lyrics.LyricsUnavailable):
        lyrics.fetch("Queen", "Bohemian Rhapsody", "", 354)

    # A person retries: the sources are asked again, and they have come back.
    monkeypatch.setattr(
        lyrics, "_fetch_remote", lambda *a, **k: lyrics.Lyrics("hey", "", "genius")
    )
    assert lyrics.fetch("Queen", "Bohemian Rhapsody", "", 354, force=True) is not None

    # That hit is now cached, and force must not stampede past a good answer.
    monkeypatch.setattr(lyrics, "_fetch_remote", _boom)
    assert lyrics.fetch("Queen", "Bohemian Rhapsody", "", 354, force=True) is not None


def test_force_does_not_re_ask_a_real_miss(cache, monkeypatch):
    """`absent` is a real answer. Only an outage is worth going back for."""
    monkeypatch.setattr(lyrics, "_fetch_remote", lambda *a, **k: None)
    assert lyrics.fetch("Nobody", "No Such Song", "", 100) is None
    monkeypatch.setattr(lyrics, "_fetch_remote", _boom)
    assert lyrics.fetch("Nobody", "No Such Song", "", 100, force=True) is None


def test_retry_reopens_a_tripped_breaker(cache, monkeypatch):
    """Someone asking again is also asking us to re-test the blocked source."""
    for _ in range(lyrics.BREAKER_THRESHOLD):
        lyrics._breaker_record("genius", ok=False)
    assert not lyrics._breaker_allows("genius")

    monkeypatch.setattr(lyrics, "_fetch_remote", _raise_unavailable)
    with pytest.raises(lyrics.LyricsUnavailable):
        lyrics.fetch("Queen", "Bohemian Rhapsody", "", 354)
    monkeypatch.setattr(lyrics, "_fetch_remote", lambda *a, **k: None)
    lyrics.fetch("Queen", "Bohemian Rhapsody", "", 354, force=True)
    assert lyrics._breaker_allows("genius")
