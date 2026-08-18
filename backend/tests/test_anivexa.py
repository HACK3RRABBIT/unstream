"""Anivexa sidecar integration — provider ordering, capability cache, quality
selection, fallback, subtitles, progress — all hermetic, no network.

The AnivexaProvider's HTTP boundary is stubbed (`anivexa._get`,
`anivexa._master_heights`, `httpx`), the provider registry is monkeypatched
via `providers`/`ordered_providers`, and the video pipeline's external edges
(yt-dlp, ffprobe, ffmpeg) are stubbed like the existing test_anime.py suite.
"""

import threading
import time

import pytest

from app import downloader as audio_downloader


@pytest.fixture(autouse=True)
def _fresh_capability_cache():
    """The capability cache is module state shared by every test in this file;
    clear it (and its flight locks) so one test's probe never leaks into the
    next — same isolation the rest of the suite uses for its in-memory state."""
    anivexa._cap_cache.clear()
    anivexa._cap_locks.clear()
    yield
from app.anime import anivexa, opensubtitles
from app.anime import downloader as anime_downloader
from app.anime import providers as providers_module
from app.anime.providers import (
    EpisodeSource,
    EpisodeStream,
    ProviderCapability,
    QualityUnavailable,
)
from app.models import Track


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _anivexa_track(episode=1, provider="anivexa", aid=20807, season=1) -> Track:
    return Track(
        id=f"1:s{season}e{episode}",
        title=f"Episode {episode}",
        artists=["Prison School"],
        album="Prison School — Season 1",
        duration_ms=24 * 60 * 60 * 1000,
        cover_url=None,
        track_number=episode,
        media="video",
        subs=["eng"],
        source_url=f"anime://{provider}/{aid}/{season}/{episode}#anilist={aid}",
    )


class _FakeProvider:
    """A minimal provider the registry can hand the downloader."""

    def __init__(self, name, streams_hls=True, available=True, raises=None):
        self.name = name
        self.streams_hls = streams_hls
        self._available = available
        self._raises = raises
        self.called = 0

    def available(self) -> bool:
        return self._available

    def resolve(self, title, year, anilist_id=None):
        return EpisodeSource(
            provider=self.name, anime_id=title, anime_title=title,
            year=year, season=0, episode=0, anilist_id=anilist_id,
        )

    def episode_stream(self, src, quality):
        self.called += 1
        if self._raises is not None:
            raise self._raises
        return EpisodeStream(provider=self.name, url=f"https://cdn/{self.name}/m.m3u8")

    def episode_count(self, src):
        return None

    def download(self, stream, dest, quality, on_progress, should_cancel, subs="eng"):
        out = dest.with_name(dest.name + ".mkv")
        out.write_bytes(b"torrent video")
        return out


def _stub_pipeline(monkeypatch, probe):
    """Fake the video pipeline's external edges; `probe` supplies heights."""
    from app import downloader

    def fake_ytdlp(stream, dest, on_progress, should_cancel, quality):
        out = dest.with_name(dest.name + ".mp4")
        out.write_bytes(b"hls stream")
        return out

    monkeypatch.setattr(anime_downloader, "_download_with_ytdlp", fake_ytdlp)
    monkeypatch.setattr(anime_downloader, "_fetch_subs", lambda s, d: None)
    monkeypatch.setattr(anime_downloader, "_mux_subtitles", lambda v, s, d: v)
    monkeypatch.setattr(anime_downloader, "_probe_height", probe)
    monkeypatch.setattr(downloader.shutil, "which", lambda name: "/usr/bin/ffmpeg")


def _stub_registry(monkeypatch, *providers):
    monkeypatch.setattr(
        providers_module, "providers", lambda order=None: list(providers)
    )


def _clock(monkeypatch):
    """A fake monotonic clock so TTL expiry is testable without sleeping."""
    class _Clock:
        now = 1000.0

        def monotonic(self):
            return _Clock.now

    monkeypatch.setattr(anivexa.time, "monotonic", _Clock().monotonic)
    return _Clock


# ── 1. Provider ordering ──────────────────────────────────────────────────────


def test_order_for_480_leads_with_anivexa(monkeypatch):
    monkeypatch.delenv("ANIME_PROVIDER_ORDER", raising=False)
    assert providers_module.order_for("480") == ["anivexa", "nyaa", "hianime"]


def test_order_for_720_leads_with_anivexa(monkeypatch):
    monkeypatch.delenv("ANIME_PROVIDER_ORDER", raising=False)
    assert providers_module.order_for("720") == ["anivexa", "nyaa", "hianime"]


def test_order_for_1080_leads_with_nyaa(monkeypatch):
    monkeypatch.delenv("ANIME_PROVIDER_ORDER", raising=False)
    assert providers_module.order_for("1080") == ["nyaa", "anivexa", "hianime"]


def test_order_for_original_leads_with_nyaa(monkeypatch):
    monkeypatch.delenv("ANIME_PROVIDER_ORDER", raising=False)
    assert providers_module.order_for("original") == ["nyaa", "anivexa", "hianime"]


def test_order_for_explicit_env_override_wins(monkeypatch):
    monkeypatch.setenv("ANIME_PROVIDER_ORDER", "hianime,nyaa")
    assert providers_module.order_for("480") == ["hianime", "nyaa"]
    assert providers_module.order_for("1080") == ["hianime", "nyaa"]


def test_chain_excluding_puts_plan_provider_first_then_quality_order(monkeypatch):
    """The download chain keeps the plan's provider first, then the quality-
    aware order — so a 720p plan on anivexa tries anivexa, then Nyaa."""
    _stub_registry(
        monkeypatch,
        _FakeProvider("nyaa"),
        _FakeProvider("anivexa"),
        _FakeProvider("hianime"),
    )
    chain = anime_downloader._chain_excluding("anivexa", set(), "720")
    assert [p.name for p in chain] == ["anivexa", "nyaa", "hianime"]
    # An explicit override reorders the whole chain.
    chain = anime_downloader._chain_excluding("anivexa", set(), "1080")
    assert [p.name for p in chain] == ["anivexa", "nyaa", "hianime"]


def test_chain_excluding_remembers_failed_providers(monkeypatch):
    _stub_registry(
        monkeypatch,
        _FakeProvider("anivexa"),
        _FakeProvider("nyaa"),
        _FakeProvider("hianime"),
    )
    chain = anime_downloader._chain_excluding("anivexa", {"anivexa"}, "720")
    assert [p.name for p in chain] == ["nyaa", "hianime"]


# ── 2. Capability model: status aggregation ───────────────────────────────────


def _filtered_episodes(provider: str, sub_episodes: list[int]) -> dict:
    """The filtered `/episodes/<prov>/<aid>` response shape."""
    return {
        "page": 1,
        "type": "filtered",
        provider: {
            "meta": {"title": "Prison School", "source": provider},
            "episodes": {
                "sub": [{"number": n, "id": f"{provider}-{n}"} for n in sub_episodes],
                "dub": [],
            },
        },
    }


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


def _stub_episodes(monkeypatch, mapping: dict[str, list[int]]):
    """`_get` answers /episodes/<prov>/<aid> with the canned episode list."""

    def fake_get(path: str, timeout=None):
        assert path.startswith("/episodes/"), f"unexpected path {path}"
        _, _, provider, aid = path.split("/")
        if provider not in mapping:
            return _FakeResp({provider: {"error": "not found"}})
        return _FakeResp(_filtered_episodes(provider, mapping[provider]))

    monkeypatch.setattr(anivexa, "_get", fake_get)
    return fake_get


def test_internal_status_ok_when_episodes_present(monkeypatch):
    _stub_episodes(monkeypatch, {"anineko": [1, 2, 3]})
    assert anivexa._internal_status("anineko", 20807, time.monotonic() + 10) == "ok"


def test_internal_status_unavailable_when_provider_missing(monkeypatch):
    _stub_episodes(monkeypatch, {})  # provider key absent -> error entry
    assert (
        anivexa._internal_status("anineko", 20807, time.monotonic() + 10)
        == "unavailable"
    )


def test_internal_status_unavailable_when_empty_sub_list(monkeypatch):
    _stub_episodes(monkeypatch, {"anineko": []})
    assert (
        anivexa._internal_status("anineko", 20807, time.monotonic() + 10)
        == "unavailable"
    )


def test_internal_status_unknown_on_provider_failure(monkeypatch):
    def boom(path, timeout=None):
        raise Exception("sidecar down")  # noqa: TRY002

    monkeypatch.setattr(anivexa, "_get", boom)
    assert anivexa._internal_status("anineko", 20807, time.monotonic() + 10) == "unknown"


def test_probe_capability_verified_aggregates_heights(monkeypatch):
    _stub_episodes(monkeypatch, {"anineko": [1, 2, 3], "animegg": [1, 2]})
    monkeypatch.setattr(anivexa, "_probe_heights", lambda p, aid, dl: [360, 720, 1080] if p == "anineko" else None)
    cap = anivexa._probe_capability(20807, 1)
    assert cap.status == "ok"
    assert cap.qualities == ["360", "720", "1080"]


def test_probe_capability_unavailable_only_when_all_authoritative(monkeypatch):
    _stub_episodes(monkeypatch, {})  # every provider authoritatively absent
    cap = anivexa._probe_capability(20807, 1)
    assert cap.status == "unavailable"


def test_probe_capability_unknown_never_becomes_unavailable(monkeypatch):
    """A probe that can't be completed (timeouts, sidecar flake) is UNKNOWN,
    never UNAVAILABLE — the anime might exist behind the flake."""
    monkeypatch.setattr(
        anivexa,
        "_internal_status",
        lambda p, aid, dl: "unknown",
    )
    cap = anivexa._probe_capability(20807, 1)
    assert cap.status == "unknown"


def test_probe_capability_ok_beats_unknown(monkeypatch):
    """If any source verifies the anime exists, the aggregate is OK even when
    another source's probe timed out — an unknown does not hide a positive."""
    monkeypatch.setattr(
        anivexa,
        "_internal_status",
        lambda p, aid, dl: "ok" if p == "anineko" else "unknown",
    )
    monkeypatch.setattr(anivexa, "_probe_heights", lambda p, aid, dl: [720])
    cap = anivexa._probe_capability(20807, 1)
    assert cap.status == "ok"


def test_provider_timeout_does_not_permanently_disable(monkeypatch):
    """A 5xx/timeout probe returns UNKNOWN, is never stored as UNAVAILABLE, and
    the provider is still attempted (status drives no skip)."""
    calls = {"n": 0}

    def flaky(path, timeout=None):
        calls["n"] += 1
        raise Exception("timeout")  # noqa: TRY002

    monkeypatch.setattr(anivexa, "_get", flaky)
    for _ in range(3):
        assert anivexa._internal_status("anineko", 20807, time.monotonic() + 10) == "unknown"
    # Each call independently reports unknown — never a cached unavailable.
    assert calls["n"] == 3


# ── 3. Capability cache ───────────────────────────────────────────────────────


def _probe_stub(monkeypatch, capability, counter):
    monkeypatch.setattr(
        anivexa,
        "_probe_capability",
        lambda aid, season: (counter.__setitem__("n", counter.get("n", 0) + 1)
                             or capability),
    )


def test_capability_verified_served_from_cache_within_ttl(monkeypatch):
    counter = {}
    _probe_stub(monkeypatch, ProviderCapability("anivexa", status="ok", qualities=["720"]), counter)
    assert anivexa.provider_capability(20807, 1).status == "ok"
    assert anivexa.provider_capability(20807, 1).status == "ok"
    assert counter["n"] == 1  # second read hits the 15-min cache


def test_capability_unavailable_reprobed_after_short_ttl(monkeypatch):
    clock = _clock(monkeypatch)
    counter = {}
    _probe_stub(monkeypatch, ProviderCapability("anivexa", status="unavailable"), counter)
    assert anivexa.provider_capability(20807, 1).status == "unavailable"
    clock.now += anivexa.UNAVAILABLE_TTL - 1
    assert counter["n"] == 1  # still inside the 2-min window
    clock.now += 2
    anivexa.provider_capability(20807, 1)
    assert counter["n"] == 2  # expired -> re-probed


def test_capability_unknown_not_cached_as_unavailable(monkeypatch):
    clock = _clock(monkeypatch)
    counter = {}
    _probe_stub(monkeypatch, ProviderCapability("anivexa", status="unknown"), counter)
    cap = anivexa.provider_capability(20807, 1)
    assert cap.status == "unknown"  # the cached value stays unknown, never unavailable
    # Short unknown TTL, then a re-probe happens — the provider is not silenced.
    clock.now += anivexa.UNKNOWN_TTL + 1
    _probe_stub(monkeypatch, ProviderCapability("anivexa", status="ok"), counter)
    assert anivexa.provider_capability(20807, 1).status == "ok"
    assert counter["n"] == 2


def test_capability_single_flight_prevents_duplicate_probes(monkeypatch):
    counter = {"n": 0}
    started = threading.Event()
    release = threading.Event()

    def slow_probe(aid, season):
        counter["n"] += 1
        started.set()
        release.wait(timeout=5)
        return ProviderCapability("anivexa", status="ok", qualities=["720"])

    monkeypatch.setattr(anivexa, "_probe_capability", slow_probe)
    results = []

    def worker():
        results.append(anivexa.provider_capability(20807, 1))

    first = threading.Thread(target=worker)
    first.start()
    assert started.wait(timeout=5)  # first thread is now inside the probe
    second = threading.Thread(target=worker)
    second.start()
    time.sleep(0.05)  # give the second thread time to block on the flight lock
    release.set()
    first.join()
    second.join()
    assert counter["n"] == 1  # single-flight: one probe, two callers
    assert all(r.status == "ok" for r in results)


def test_capability_cache_lru_bound(monkeypatch):
    clock = _clock(monkeypatch)
    counter = {}
    _probe_stub(monkeypatch, ProviderCapability("anivexa", status="ok"), counter)
    assert anivexa.CACHE_MAX >= 1
    original_max = anivexa.CACHE_MAX
    try:
        anivexa.CACHE_MAX = 2
        anivexa.provider_capability(1, 1)
        anivexa.provider_capability(2, 1)
        anivexa.provider_capability(3, 1)
        with anivexa._cap_guard:
            assert len(anivexa._cap_cache) <= 2
            assert (1, 1) not in anivexa._cap_cache  # oldest evicted
            assert (2, 1) in anivexa._cap_cache
            assert (3, 1) in anivexa._cap_cache
    finally:
        anivexa.CACHE_MAX = original_max


# ── 4. Anivexa response normalization ─────────────────────────────────────────


def _watch(streams=None, subtitles=None, headers=None):
    return {
        "streams": streams or [],
        "subtitles": subtitles or [],
        "headers": headers or {},
    }


def test_filter_direct_keeps_hls_and_mp4():
    watch = _watch(streams=[
        {"url": "https://cdn/x/master.m3u8", "type": "hls"},
        {"url": "https://cdn/x/video.mp4", "type": "mp4"},
        {"url": "https://site/player/embed", "type": "embed"},
        {"url": "https://site/player.php?id=1", "type": "player"},
        {"url": "https://site/iframe", "type": "iframe"},
        {"url": "https://cdn/x/frame001.jpg", "type": "hls"},
        {"url": "not-a-url", "type": "hls"},
    ])
    direct = anivexa._filter_direct(watch)
    assert [s["type"] for s in direct] == ["hls", "mp4"]
    assert [s["url"] for s in direct] == [
        "https://cdn/x/master.m3u8", "https://cdn/x/video.mp4",
    ]


def test_master_heights_parses_resolution_lines(monkeypatch):
    master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=1280x720\n"
        "index_720.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1920x1080\n"
        "index_1080.m3u8\n"
    )
    monkeypatch.setattr(anivexa.httpx, "get", lambda *a, **k: _FakeHttp(master))
    assert anivexa._master_heights("https://cdn/x/master.m3u8", {}) == [720, 1080]


def test_master_heights_rejects_non_playlist(monkeypatch):
    monkeypatch.setattr(anivexa.httpx, "get", lambda *a, **k: _FakeHttp("<html>challenge</html>"))
    assert anivexa._master_heights("https://cdn/x/master.m3u8", {}) is None


def test_master_heights_rejects_slideshow(monkeypatch):
    slideshow = "#EXTM3U\n#EXTINF:5,\nhttps://cdn/x/frame001.jpg\n"
    monkeypatch.setattr(anivexa.httpx, "get", lambda *a, **k: _FakeHttp(slideshow))
    assert anivexa._master_heights("https://cdn/x/master.m3u8", {}) == []


def test_master_heights_unknown_on_fetch_error(monkeypatch):
    def boom(*a, **k):
        raise Exception("refused")  # noqa: TRY002

    monkeypatch.setattr(anivexa.httpx, "get", boom)
    assert anivexa._master_heights("https://cdn/x/master.m3u8", {}) is None


def test_pick_en_subtitle_prefers_english():
    watch = _watch(
        subtitles=[
            {"url": "https://cdn/x/es.vtt", "label": "Spanish", "srclang": "es"},
            {"url": "https://cdn/x/en.vtt", "label": "English", "srclang": "en"},
        ],
    )
    assert anivexa._pick_en_subtitle(watch) == "https://cdn/x/en.vtt"


def test_pick_en_subtitle_reads_stream_subtitles():
    watch = _watch(streams=[{"url": "https://cdn/x/m.m3u8", "type": "hls",
                             "subtitles": [{"url": "https://cdn/x/en.vtt", "label": "English"}]}])
    assert anivexa._pick_en_subtitle(watch) == "https://cdn/x/en.vtt"


def test_pick_en_subtitle_none_when_absent():
    assert anivexa._pick_en_subtitle(_watch()) is None


class _FakeHttp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


# ── 5. Quality selection ──────────────────────────────────────────────────────


def _stream_from(monkeypatch, heights, stream_type="hls", url="https://cdn/x/master.m3u8"):
    monkeypatch.setattr(anivexa, "_master_heights", lambda u, h: heights)
    watch = _watch(streams=[{"url": url, "type": stream_type}])
    return anivexa._stream_from("anikoto", watch, "720", 3)


def test_stream_from_exact_height_accepted(monkeypatch):
    stream = _stream_from(monkeypatch, [360, 720, 1080])
    assert stream.url == "https://cdn/x/master.m3u8"
    assert stream.provider == "anivexa"


def test_stream_from_no_silent_upgrade(monkeypatch):
    """A master with only 1080 must not serve a 720 request at 1080."""
    from app.anime.providers import QualityUnavailable

    with pytest.raises(QualityUnavailable):
        _stream_from(monkeypatch, [360, 1080])


def test_stream_from_no_silent_downgrade(monkeypatch):
    """A master with only 360 must not serve a 720 request at 360."""
    with pytest.raises(QualityUnavailable):
        _stream_from(monkeypatch, [360])


def test_stream_from_unreadable_master_is_unknown_not_absent(monkeypatch):
    """A master that can't be fetched is UNKNOWN — the download proceeds and
    the post-download ffprobe check is the enforcer."""
    stream = _stream_from(monkeypatch, None)
    assert stream.url == "https://cdn/x/master.m3u8"


def test_stream_from_mp4_accepted_height_verified_later(monkeypatch):
    """animegg direct mp4 has no ladder to read up front; it is served and the
    post-download height check is the gate."""
    stream = _stream_from(monkeypatch, None, stream_type="mp4", url="https://cdn/x/video.mp4")
    assert stream.url == "https://cdn/x/video.mp4"


def test_stream_from_watch_headers_carried(monkeypatch):
    monkeypatch.setattr(anivexa, "_master_heights", lambda u, h: [720])
    watch = _watch(streams=[{"url": "https://cdn/x/m.m3u8", "type": "hls", "referer": "https://site/"}],
                   headers={"User-Agent": "UA"})
    stream = anivexa._stream_from("anikoto", watch, "720", 3)
    assert stream.headers.get("Referer") == "https://site/"
    assert stream.headers.get("User-Agent") == "UA"


# ── 6. Fallback through the downloader ────────────────────────────────────────


def test_anivexa_failure_falls_back_to_nyaa(monkeypatch, tmp_path):
    from app.anime.providers import QualityUnavailable

    anivexa_provider = _FakeProvider("anivexa", raises=QualityUnavailable("no 720p on anivexa"))
    nyaa = _FakeProvider("nyaa", streams_hls=False)
    _stub_registry(monkeypatch, anivexa_provider, nyaa)
    _stub_pipeline(monkeypatch, lambda p: 720)

    meta: dict = {}
    out = anime_downloader.download_video_track(
        _anivexa_track(), tmp_path, lambda s, f: None, "720", None, None, meta=meta
    )
    assert out.exists()
    assert meta["provider"] == "nyaa"
    assert meta["served_quality"] == "720p"


def test_nyaa_failure_falls_back_to_hianime(monkeypatch, tmp_path):
    from app.anime.providers import QualityUnavailable

    nyaa = _FakeProvider("nyaa", streams_hls=False, raises=QualityUnavailable("no 480p on nyaa"))
    hianime = _FakeProvider("hianime")
    _stub_registry(monkeypatch, nyaa, hianime)
    _stub_pipeline(monkeypatch, lambda p: 480)

    meta: dict = {}
    out = anime_downloader.download_video_track(
        _anivexa_track(provider="nyaa"), tmp_path, lambda s, f: None, "480", None, None, meta=meta
    )
    assert out.exists()
    assert meta["provider"] == "hianime"


def test_fallback_provider_is_reanchored_with_season_title(monkeypatch, tmp_path):
    """The plan URL carries the season title (#anilist=...&title=...) so a
    fallback provider is re-anchored to a source in its own domain.

    Real defect this pins: anivexa leads the 480/720 chain and its anime_id is
    the numeric AniList id. Nyaa was asked with anime_id='21' and searched the
    number instead of 'Prison School'. The re-anchor hands the fallback the
    season title so it can find the show."""
    from app.anime.providers import QualityUnavailable

    seen = {}

    class AnivexaFail:
        name = "anivexa"
        streams_hls = True

        def available(self):
            return True

        def episode_stream(self, src, quality):
            raise QualityUnavailable("anivexa cannot serve this here")

        def resolve(self, title, year, anilist_id=None):
            return EpisodeSource(
                provider="anivexa", anime_id=str(anilist_id), anime_title=title,
                year=year, season=0, episode=0, anilist_id=anilist_id,
            )

    class NyaaCatches:
        name = "nyaa"
        streams_hls = False

        def available(self):
            return True

        def resolve(self, title, year, anilist_id=None):
            return EpisodeSource(
                provider="nyaa", anime_id=title, anime_title=title,
                year=year, season=0, episode=0, anilist_id=anilist_id,
            )

        def episode_stream(self, src, quality):
            seen["anime_id"] = src.anime_id
            seen["anilist_id"] = src.anilist_id
            return EpisodeStream(provider="nyaa", url="magnet:?xt=urn:btih:abc")

        def download(self, stream, dest, quality, on_progress, should_cancel, subs="eng"):
            out = dest.with_name(dest.name + ".mp4")
            out.write_bytes(b"video")
            return out

    _stub_registry(monkeypatch, AnivexaFail(), NyaaCatches())
    _stub_pipeline(monkeypatch, lambda p: 720)

    track = _anivexa_track()
    # The real route appends the season title; encode it the same way.
    track.source_url = f"anime://anivexa/20807/1/1#anilist=20807&title=Prison School"
    meta: dict = {}
    out = anime_downloader.download_video_track(
        track, tmp_path, lambda s, f: None, "720", None, None, meta=meta
    )
    assert out.exists()
    assert meta["provider"] == "nyaa"
    # Nyaa was handed the title, not the numeric AniList id — and it still
    # kept the AniList id for id-keyed providers to use.
    assert seen["anime_id"] == "Prison School"
    assert seen["anilist_id"] == 20807


def test_capability_unavailable_skips_without_probe(monkeypatch, tmp_path):
    """A verified UNAVAILABLE provider is hopped without calling episode_stream."""
    from app.anime.providers import ProviderCapability

    class CapAnivexa:
        name = "anivexa"
        streams_hls = True

        def available(self):
            return True

        def capabilities(self, src):
            return ProviderCapability("anivexa", status="unavailable")

        def episode_stream(self, src, quality):
            raise AssertionError("an unavailable provider must not be probed")

        def resolve(self, title, year, anilist_id=None):
            return EpisodeSource(provider="anivexa", anime_id=str(anilist_id), anime_title=title,
                                 year=year, season=0, episode=0, anilist_id=anilist_id)

    nyaa = _FakeProvider("nyaa", streams_hls=False)
    _stub_registry(monkeypatch, CapAnivexa(), nyaa)
    _stub_pipeline(monkeypatch, lambda p: 720)
    meta: dict = {}
    out = anime_downloader.download_video_track(
        _anivexa_track(), tmp_path, lambda s, f: None, "720", None, None, meta=meta
    )
    assert out.exists()
    assert meta["provider"] == "nyaa"


def test_capability_unknown_never_skips_provider(monkeypatch, tmp_path):
    """An UNKNOWN capability must not prevent the provider being attempted."""
    from app.anime.providers import ProviderCapability

    class UnknownAnivexa:
        name = "anivexa"
        streams_hls = True

        def available(self):
            return True

        def capabilities(self, src):
            return ProviderCapability("anivexa", status="unknown")

        def episode_stream(self, src, quality):
            return EpisodeStream(provider="anivexa", url="https://cdn/720.m3u8")

    _stub_registry(monkeypatch, UnknownAnivexa())
    _stub_pipeline(monkeypatch, lambda p: 720)
    meta: dict = {}
    out = anime_downloader.download_video_track(
        _anivexa_track(), tmp_path, lambda s, f: None, "720", None, None, meta=meta
    )
    assert out.exists()
    assert meta["provider"] == "anivexa"


# ── 7. Subtitle behavior ──────────────────────────────────────────────────────


def test_opensubtitles_disabled_without_key(monkeypatch):
    monkeypatch.setattr(opensubtitles, "API_KEY", "")
    assert opensubtitles.available() is False


def test_opensubtitles_enabled_with_key(monkeypatch):
    monkeypatch.setattr(opensubtitles, "API_KEY", "test-key")
    assert opensubtitles.available() is True


def test_opensubtitles_consulted_only_when_needed(monkeypatch, tmp_path):
    """With a key set, an HLS source that shipped no English subtitle is rescued
    by OpenSubtitles (validated before muxing)."""
    monkeypatch.setattr(opensubtitles, "API_KEY", "test-key")
    calls = {"fetch": 0}
    srt = tmp_path / "sub.opensubtitles.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:04,000\nHello\n")

    def fake_fetch(imdb_id, title, season, episode, dest):
        calls["fetch"] += 1
        return srt

    monkeypatch.setattr(opensubtitles, "available", lambda: True)
    monkeypatch.setattr(opensubtitles, "fetch_english", fake_fetch)

    served = []
    monkeypatch.setattr(
        anime_downloader,
        "_finalize_subtitles",
        lambda video, sub, requested, dest: served.append(sub) or video,
    )
    _stub_registry(monkeypatch, _FakeProvider("anivexa"))
    _stub_pipeline(monkeypatch, lambda p: 720)
    out = anime_downloader.download_video_track(
        _anivexa_track(), tmp_path, lambda s, f: None, "720", None, None
    )
    assert out.exists()
    assert calls["fetch"] == 1
    assert served == [srt]


def test_opensubtitles_not_consulted_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(opensubtitles, "API_KEY", "")
    calls = {"fetch": 0}

    def fake_fetch(*a, **k):
        calls["fetch"] += 1
        return None

    monkeypatch.setattr(opensubtitles, "available", lambda: False)
    monkeypatch.setattr(opensubtitles, "fetch_english", fake_fetch)
    _stub_registry(monkeypatch, _FakeProvider("anivexa"))
    _stub_pipeline(monkeypatch, lambda p: 720)
    out = anime_downloader.download_video_track(
        _anivexa_track(), tmp_path, lambda s, f: None, "720", None, None
    )
    assert out.exists()
    assert calls["fetch"] == 0


def test_embedded_subtitle_preferred_over_opensubtitles(monkeypatch, tmp_path):
    """A stream that carries its own English subtitle is used; OpenSubtitles is
    not consulted."""
    monkeypatch.setattr(opensubtitles, "API_KEY", "test-key")
    calls = {"fetch": 0}
    monkeypatch.setattr(opensubtitles, "available", lambda: True)
    monkeypatch.setattr(
        opensubtitles, "fetch_english", lambda *a, **k: calls.__setitem__("fetch", calls["fetch"] + 1)
    )

    class SubbedAnivexa:
        name = "anivexa"
        streams_hls = True

        def available(self):
            return True

        def episode_stream(self, src, quality):
            return EpisodeStream(
                provider="anivexa",
                url="https://cdn/720.m3u8",
                subtitle_url="https://cdn/en.vtt",
            )

    _stub_registry(monkeypatch, SubbedAnivexa())
    _stub_pipeline(monkeypatch, lambda p: 720)

    def fake_fetch_subs(stream, dest):
        sub = tmp_path / "ep.srt"
        sub.write_text("1\n00:00:01,000 --> 00:00:04,000\nHello\n")
        return sub

    monkeypatch.setattr(anime_downloader, "_fetch_subs", fake_fetch_subs)

    out = anime_downloader.download_video_track(
        _anivexa_track(), tmp_path, lambda s, f: None, "720", None, None
    )
    assert out.exists()
    assert calls["fetch"] == 0  # the stream's own subtitle won


# ── 8. Provider-search progress ───────────────────────────────────────────────


def test_provider_progress_reports_real_transitions(monkeypatch, tmp_path):
    """The callback fires once per real provider attempt — never on a timer."""
    from app.anime.providers import QualityUnavailable

    anivexa_provider = _FakeProvider("anivexa", raises=QualityUnavailable("no 720p"))
    nyaa = _FakeProvider("nyaa", streams_hls=False)
    _stub_registry(monkeypatch, anivexa_provider, nyaa)
    _stub_pipeline(monkeypatch, lambda p: 720)

    events = []
    meta: dict = {}
    anime_downloader.download_video_track(
        _anivexa_track(), tmp_path, lambda s, f: None, "720", None, None, meta=meta,
        on_provider_progress=lambda c, t, n: events.append((c, t, n)),
    )
    assert meta["provider"] == "nyaa"
    # (start marker, anivexa attempt, nyaa attempt) — 2 providers in the chain.
    assert events == [
        (0, 2, None),
        (1, 2, "anivexa"),
        (2, 2, "nyaa"),
    ]


def test_provider_progress_counts_skipped_provider(monkeypatch, tmp_path):
    """An up-front capability skip still advances the counter — it is a real
    source check, just one that needed no episode probe."""
    from app.anime.providers import ProviderCapability

    class CapAnivexa:
        name = "anivexa"
        streams_hls = True

        def available(self):
            return True

        def capabilities(self, src):
            return ProviderCapability("anivexa", status="unavailable")

    nyaa = _FakeProvider("nyaa", streams_hls=False)
    _stub_registry(monkeypatch, CapAnivexa(), nyaa)
    _stub_pipeline(monkeypatch, lambda p: 720)

    events = []
    anime_downloader.download_video_track(
        _anivexa_track(), tmp_path, lambda s, f: None, "720", None, None,
        on_provider_progress=lambda c, t, n: events.append((c, t, n)),
    )
    assert events == [
        (0, 2, None),
        (1, 2, None),   # anivexa skipped — checked, not attempted
        (2, 2, "nyaa"),
    ]


# ── 9. Existing behavior compatibility ────────────────────────────────────────


def test_parse_source_url_with_anilist_fragment():
    src = anime_downloader.parse_source_url(
        "anime://anivexa/20807/1/3#anilist=20807"
    )
    assert src.provider == "anivexa"
    assert src.anime_id == "20807"
    assert src.anilist_id == 20807
    assert src.season == 1
    assert src.episode == 3


def test_parse_source_url_without_fragment_keeps_anilist_none():
    src = anime_downloader.parse_source_url("anime://nyaa/Naruto/1/3")
    assert src.anilist_id is None


def test_resolve_signatures_accept_anilist_id(monkeypatch):
    """All three providers accept the anilist_id kwarg (hianime's search stub
    returns a category slug so resolve does no network)."""
    from app.anime import hianime, nyaa

    monkeypatch.setattr(
        hianime, "_json",
        lambda path, **kw: {"html": '<a href="/category/prison-school">x</a>'},
    )
    for provider in (nyaa.NyaaProvider(), hianime.HianimeProvider(), anivexa.AnivexaProvider()):
        src = provider.resolve("Prison School", 2015, anilist_id=20807)
        assert src.anime_title == "Prison School"
