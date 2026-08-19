"""Per-episode quality availability — Nyaa episode_resolutions, the anivexa
episode-aware capability cache, and the /qualities endpoint. All hermetic, no
network: Nyaa's HTTP boundary is stubbed (`nyaa._client.get`), the sidecar's is
too (`anivexa._get`, `anivexa._probe_heights`), and the endpoint's franchise
walk (`anilist.franchise`) and provider registry are monkeypatched. Mirrors the
anivexa.test cache fixtures (fake monotonic clock) and the test_anime.py route
TestClient pattern.
"""

import time
import threading

import pytest

from app.anime import anivexa, nyaa, providers as providers_module
from app.anime.providers import ProviderCapability


@pytest.fixture(autouse=True)
def _fresh_caches():
    """Clear the capability caches (and their flight locks) between tests."""
    anivexa._cap_cache.clear()
    anivexa._cap_locks.clear()
    anivexa._ep_cache.clear()
    anivexa._ep_locks.clear()
    nyaa._ep_resolutions_cache.clear()
    nyaa._ep_resolutions_locks.clear()
    yield


def _clock(monkeypatch):
    """A fake monotonic clock so per-episode TTL expiry is testable.

    Both cache layers (anivexa and nyaa's) keep their own `time` module, so
    the same fake clock drives both.
    """
    class _Clock:
        now = 1000.0

        def monotonic(self):
            return self.now

    clock = _Clock()
    monkeypatch.setattr(anivexa.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(nyaa.time, "monotonic", clock.monotonic)
    return clock


def _nyaa_page(*rows: str) -> str:
    return (
        '<table class="torrent-list"><tbody>'
        "<tr><th>Category</th><th>Name</th></tr>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _nyaa_empty_page() -> str:
    """A genuine Nyaa "No results found" response — the ONLY authoritative
    empty signal. Real Nyaa answers an empty search with this `<h3>` marker and
    no torrent-list table at all (verified live). A header-only torrent table is
    NOT this; `_response_kind` treats that as a block page."""
    return "<h3>No results found</h3>"


def _nyaa_row(torrent_id: int, title: str, magnet: str, seeders: int) -> str:
    return (
        '<tr><td><a title="Anime - English-translated"></a></td>'
        f'<td colspan="2"><a href="/view/{torrent_id}" '
        f'title="{title}"></a>'
        f'<a href="magnet:?xt=urn:btih:{magnet}"></a></td>'
        f'<td class="text-center">{seeders}</td>'
        "<td>100.0 MiB</td></tr>"
    )


class _FakeResp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


def _stub_nyaa(monkeypatch, pages):
    """Feed `episode_resolutions` canned search pages (one per HTTP call).

    `episode_resolutions` makes two Nyaa HTTP calls (the SxxExx query, then the
    bare-number query), so a test must provide one page per call — extra pages
    are ignored.

    Note the httpx call site is `get(url, params={...})`, so the fake's kwargs
    are `{"params": <dict>}` — the query lives under `kwargs["params"]["q"]`.
    """
    responses = [_FakeResp(_nyaa_page(*rows)) for rows in pages]

    def fake_get(*a, **kwargs):
        # Serve one page per query in order; when the test supplied fewer pages
        # than `episode_resolutions`'s two queries, reuse the last page so the
        # same releases show for both queries the search issues.
        if responses:
            page = responses[0]
            if len(responses) > 1:
                responses.pop(0)
            return page
        return _FakeResp(_nyaa_page())

    monkeypatch.setattr(nyaa._client, "get", fake_get)


def _episode_src(anime_id="One Piece", season=1, episode=1100):
    from app.anime.providers import EpisodeSource

    return EpisodeSource(
        provider="nyaa", anime_id=anime_id, anime_title=anime_id,
        year=1999, season=season, episode=episode,
    )


# ── Nyaa: episode_resolutions ───────────────────────────────────────────────

def test_nyaa_episode_unions_singles_and_batches(monkeypatch):
    """Seeded singles + batches both count toward the discovered resolutions."""
    # First query (S01E1100) returns a 720p single; the bare-number fallback
    # returns a 1080p batch and a 480p single.
    _stub_nyaa(monkeypatch, [
        [
            _nyaa_row(1, "[ToonsHub] One Piece - 1100 (720p)", "a", 30),
        ],
        [
            _nyaa_row(2, "[Hxod] One Piece 1001-1200 [1080p]", "b", 60),
            _nyaa_row(3, "[SubsPlease] One Piece - 1100 (480p)", "c", 70),
        ],
    ])
    got = nyaa.NyaaProvider().episode_resolutions(_episode_src(), 1100)
    # The contract is the union across both queries — exact discovery order
    # (singles before batches within a page) is not load-bearing.
    assert sorted(got, key=int) == ["480", "720", "1080"]


def test_nyaa_episode_zero_seeders_still_count_as_releases(monkeypatch):
    """A 0-seeder row is not unobtainable — it proves the resolution was
    released (a swarm can reseed tomorrow), so it counts toward discovery.
    Regression for the intermittent empty quality list: filtering 0-seeder rows
    out made a seeded batch search return [] and blank the picker."""
    _stub_nyaa(monkeypatch, [
        [
            _nyaa_row(1, "[ToonsHub] One Piece - 1100 (720p)", "a", 0),
            _nyaa_row(2, "[Judas] One Piece - 1100 [1080p]", "b", 0),
        ],
    ])
    got = nyaa.NyaaProvider().episode_resolutions(_episode_src(), 1100)
    assert got == ["720", "1080"]  # 0-seeder rows still prove the resolutions


def test_nyaa_episode_resolution_parsing_is_explicit(monkeypatch):
    """Only explicit NNNp / WxH markers count — not bitrates or bare digits."""
    _stub_nyaa(monkeypatch, [
        [
            _nyaa_row(1, "[X] Show - 01 [48000Hz FLAC]", "a", 10),
            _nyaa_row(2, "[X] Show - 01 (48000 Hz)", "b", 10),
            _nyaa_row(3, "[X] Show 001-480", "c", 10),
            _nyaa_row(4, "[X] Show 480", "d", 10),
            _nyaa_row(5, "[X] Show - 01 [480p].mkv", "e", 10),
            _nyaa_row(6, "[X] Show - 01 (1280x720)", "f", 10),
            _nyaa_row(7, "[X] Show - 01 [1080p HEVC]", "g", 10),
        ],
    ])
    got = nyaa.NyaaProvider().episode_resolutions(_episode_src(), 1)
    assert got == ["480", "720", "1080"]


def test_nyaa_episode_season_tag_first_query(monkeypatch):
    """The SxxExx query is issued first when the season is known, then the bare
    number — reused from _search_episode's query loop."""
    seen = []

    def fake_get(url, **kwargs):
        params = kwargs.get("params") or {}
        seen.append(params.get("q"))
        return _FakeResp(_nyaa_empty_page())  # genuine "No results found" both times

    monkeypatch.setattr(nyaa._client, "get", fake_get)
    nyaa.NyaaProvider().episode_resolutions(_episode_src(season=3), 10)
    assert seen == ["One Piece S03E10", "One Piece 10"]


def test_nyaa_episode_cache_serves_repeat_without_requery(monkeypatch):
    """A second call for the SAME episode is served from cache — no requery."""
    calls = {"n": 0}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        rows = [_nyaa_row(1, "[SubsPlease] Show - 01 (720p)", "a", 20)]
        return _FakeResp(_nyaa_page(*rows))

    monkeypatch.setattr(nyaa._client, "get", fake_get)
    pr = nyaa.NyaaProvider()
    got = pr.episode_resolutions(_episode_src(), 1)
    assert got == ["720"]
    got = pr.episode_resolutions(_episode_src(), 1)  # cached
    assert got == ["720"]
    assert calls["n"] == 2  # one refresh per query, from the first call only


def test_nyaa_episode_cache_does_not_collide_across_episodes(monkeypatch):
    """The key includes the episode — ep 1's cache never serves ep 2."""
    calls = {"urls": []}

    def fake_get(url, **kwargs):
        calls["urls"].append(kwargs.get("params") or {})
        # A genuine empty search for both queries.
        return _FakeResp(_nyaa_empty_page())

    monkeypatch.setattr(nyaa._client, "get", fake_get)
    pr = nyaa.NyaaProvider()
    pr.episode_resolutions(_episode_src(), 1)
    pr.episode_resolutions(_episode_src(), 2)
    # Both probed fresh (separate keys): 2 queries each = 4 HTTP calls.
    assert len(calls["urls"]) == 4


def test_nyaa_episode_cache_single_flights_concurrent_same_key(monkeypatch):
    """Two threads probing the SAME episode serialize — one Nyaa search total."""
    calls = {"n": 0}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        return _FakeResp(_nyaa_page(_nyaa_row(1, "[S] Show - 01 (720p)", "a", 9)))

    monkeypatch.setattr(nyaa._client, "get", fake_get)
    pr = nyaa.NyaaProvider()
    results: list[list[str]] = []
    errors: list[Exception] = []

    def run():
        try:
            results.append(pr.episode_resolutions(_episode_src(), 1))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    # Release both threads at once. Whichever wins the single-flight lock probes
    # (2 Nyaa queries); the other blocks, then reads the cached result. Timing
    # can't change the contract: the key's probe happens exactly once.
    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert all(r == ["720"] for r in results)
    assert calls["n"] == 2  # one HTTP query pair, shared across both threads


def test_nyaa_episode_empty_result_is_cached_briefly_then_requeried(monkeypatch):
    """An empty set (nothing seeded) is re-checked after a minute, not 15."""
    clock = _clock(monkeypatch)  # now = 1000.0
    calls = {"n": 0}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        return _FakeResp(_nyaa_empty_page())

    monkeypatch.setattr(nyaa._client, "get", fake_get)
    pr = nyaa.NyaaProvider()
    pr.episode_resolutions(_episode_src(season=1), 1)
    assert calls["n"] == 2  # both queries probed
    pr.episode_resolutions(_episode_src(season=1), 1)
    assert calls["n"] == 2  # cached as empty (< 60s)
    clock.now += 61
    pr.episode_resolutions(_episode_src(season=1), 1)
    assert calls["n"] == 4  # expired after a minute: re-probed


def test_nyaa_episode_probe_error_never_becomes_a_verdict(monkeypatch):
    """A failed probe raises ProviderError and is cached as UNKNOWN — a repeat
    within the window keeps raising (never an empty verdict); after the TTL the
    search re-runs and recovers."""
    from app.models import ProviderError

    class Boom:
        def raise_for_status(self):
            raise RuntimeError("network down")

    clock = _clock(monkeypatch)
    failing = {"on": True}

    def fake_get(url, **kwargs):
        if failing["on"]:
            return Boom()
        return _FakeResp(_nyaa_page(_nyaa_row(1, "[X] S - 01 (720p)", "a", 25)))

    monkeypatch.setattr(nyaa._client, "get", fake_get)
    pr = nyaa.NyaaProvider()
    with pytest.raises(ProviderError):
        pr.episode_resolutions(_episode_src(), 1)
    with pytest.raises(ProviderError):  # still unknown within the window
        pr.episode_resolutions(_episode_src(), 1)
    failing["on"] = False
    clock.now += 61  # unknown TTL (60s) expires
    got = pr.episode_resolutions(_episode_src(), 1)
    assert got == ["720"]  # fresh probe recovered


# ── empty vs unknown: an empty discovery may ONLY mean "the provider ran a
#    complete, authoritative search and found no release at all". A timeout,
#    network error, block page, seed churn, or a one-query miss must NEVER
#    become `qualities: []` — it is unknown. The three verdicts (verified /
#    genuinely-empty / unknown) must stay distinguishable, and only the first
#    and second may ever render an empty picker. ──────────────────────────────

def test_nyaa_episode_probe_reports_verified_qualities(monkeypatch):
    """A healthy probe returns the resolutions of every release that names the
    episode — singles and batches alike, seeded or not. A 0-seeder row still
    proves the resolution exists (a swarm can reseed tomorrow)."""
    _stub_nyaa(monkeypatch, [
        [
            _nyaa_row(1, "[X] Show - 01 [720p]", "a", 50),
            _nyaa_row(2, "[X] Show - 01 [1080p]", "b", 40),
            _nyaa_row(3, "[X] Show - 01 [480p]", "c", 0),  # 0 seeders — released
        ],
    ])
    got = nyaa.NyaaProvider().episode_resolutions(_episode_src(), 1)
    assert sorted(got, key=int) == ["480", "720", "1080"]


def test_nyaa_episode_all_0_seeders_is_verified_not_empty(monkeypatch):
    """Every release for the episode is currently 0-seeder — that is NOT an
    empty discovery. Seed counts churn; the resolutions exist."""
    _stub_nyaa(monkeypatch, [
        [
            _nyaa_row(1, "[X] Show - 01 [1080p]", "b", 0),
            _nyaa_row(2, "[X] Show - 01 [720p]", "a", 0),
        ],
    ])
    got = nyaa.NyaaProvider().episode_resolutions(_episode_src(), 1)
    assert got == ["1080", "720"]  # never []


def test_nyaa_episode_genuinely_empty_search_returns_brackets(monkeypatch):
    """Every query answering a real Nyaa 'No results found' page IS genuinely
    empty — [] is the only authoritative-empty answer. (`_stub_nyaa` with zero
    rows produces a header-only table, which is a block page — so feed the real
    empty page directly.)"""
    calls = {"n": 0}

    def counting_get(*a, **k):
        calls["n"] += 1
        return _FakeResp(_nyaa_empty_page())

    monkeypatch.setattr(nyaa._client, "get", counting_get)
    pr = nyaa.NyaaProvider()
    assert pr.episode_resolutions(_episode_src(), 1) == []  # the ONLY empty case
    # And it is cached as a verdict — an immediate repeat is served without
    # re-probing (what "authoritative empty" means; the 60s requery is covered
    # by test_nyaa_episode_empty_result_is_cached_briefly_then_requeried).
    assert pr.episode_resolutions(_episode_src(), 1) == []
    assert calls["n"] == 2  # one probe (both queries); the repeat was cached


def test_nyaa_episode_one_query_hit_one_empty_still_returns_hit(monkeypatch):
    """A genuine-empty answer for ONE query must not erase the resolutions the
    OTHER query proved — this is the exact mechanism the intermittent blank
    picker exploited."""
    pages = {
        "One Piece S01E01": [_nyaa_row(1, "[X] Show - 01 [1080p]", "b", 60)],
        "One Piece 1": "empty",
    }

    def fake_get(url, **kwargs):
        q = kwargs.get("params", {}).get("q", "")
        p = pages[q]
        if p == "empty":
            return _FakeResp(_nyaa_empty_page())
        return _FakeResp(_nyaa_page(*p))

    monkeypatch.setattr(nyaa._client, "get", fake_get)
    got = nyaa.NyaaProvider().episode_resolutions(_episode_src(), 1)
    assert got == ["1080"]  # never an empty verdict


def test_nyaa_episode_probe_timeout_is_unknown_not_empty(monkeypatch):
    """A network timeout raises ProviderError — the caller sees 'couldn't ask',
    never 'nothing released'."""
    from app.models import ProviderError

    def timeout(*a, **k):
        raise TimeoutError("nyaa timed out")

    monkeypatch.setattr(nyaa._client, "get", timeout)
    with pytest.raises(ProviderError):
        nyaa.NyaaProvider().episode_resolutions(_episode_src(), 1)


def test_nyaa_episode_probe_http_error_is_unknown_not_empty(monkeypatch):
    """An HTTP 50x/network failure is a provider failure — unknown, not empty."""
    from app.models import ProviderError

    class _Err:
        def raise_for_status(self):
            raise Exception("500 Internal Server Error")  # noqa: TRY002

    monkeypatch.setattr(nyaa._client, "get", lambda *a, **k: _Err())
    with pytest.raises(ProviderError):
        nyaa.NyaaProvider().episode_resolutions(_episode_src(), 1)


def test_nyaa_episode_block_page_normalizes_to_unknown(monkeypatch):
    """An HTTP-200 interstitial that is not a real torrent listing cannot be
    read as 'no releases' — the probe must degrade to unknown, never []."""
    from app.models import ProviderError

    html = '<html><head><title>Just a moment...</title></head><body></body></html>'
    monkeypatch.setattr(nyaa._client, "get", lambda *a, **k: _FakeResp(html))
    with pytest.raises(ProviderError):
        nyaa.NyaaProvider().episode_resolutions(_episode_src(), 1)


def test_nyaa_episode_miss_is_unknown_not_empty(monkeypatch):
    """A real listing whose rows name NO episode (a title collision, or the
    episode's rows below the fold) is a miss — it must not read as empty."""
    from app.models import ProviderError

    # A listing of unrelated episodes (E02/E03) for a search that wanted E01.
    _stub_nyaa(monkeypatch, [
        [
            _nyaa_row(1, "[X] Show - 02 [1080p]", "b", 90),
            _nyaa_row(2, "[X] Show - 03 [720p]", "a", 80),
        ],
    ])
    with pytest.raises(ProviderError):
        nyaa.NyaaProvider().episode_resolutions(_episode_src(), 1)


# ── /qualities endpoint: the three verdicts stay distinguishable ─────────────

def test_qualities_endpoint_nyaa_probe_error_is_unknown_not_ok(monkeypatch):
    """When the Nyaa probe fails, the endpoint reports status unknown + null
    qualities — never ok + [] — so the frontend shows 'couldn't determine'."""
    from app.models import ProviderError

    class Nyaa1:
        name = "nyaa"
        streams_hls = False

        def available(self):
            return True

    class OS:
        name = "hianime"
        streams_hls = True

        def available(self):
            return True

    monkeypatch.setattr(providers_module, "providers", lambda: [Nyaa1(), OS()])
    monkeypatch.setattr(
        nyaa.NyaaProvider,
        "episode_resolutions",
        lambda self, src, ep: (_ for _ in ()).throw(
            ProviderError("nyaa unreachable")
        ),
    )
    resp = _get_qualities(_call_qualities(monkeypatch))
    assert resp.status_code == 200
    nyaa_provider = next(p for p in resp.json()["providers"] if p["name"] == "nyaa")
    assert nyaa_provider["status"] == "unknown"
    assert nyaa_provider["qualities"] is None  # never hides, never []


# ── anivexa: episode-aware capability ───────────────────────────────────────

class _JsonResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


def _stub_episodes(monkeypatch, mapping):
    """Stub `anivexa._get` for /episodes/<name>/<aid> and /watch/... paths.

    `mapping` is provider name -> list of available episode numbers. /episodes
    answers with the sidecar's filtered shape; /watch/<p>/<aid>/sub/<p>-<n>
    answers with a single HLS stream so `_probe_heights` can read a master.
    """

    def fake_get(path, **kwargs):
        # /episodes/<name>/<aid> and /watch/<name>/<aid>/sub/<name>-<ep> both
        # begin with a leading slash; strip it so the head is the first segment.
        head, _, rest = path.lstrip("/").partition("/")
        if head == "episodes":
            name = rest.split("/", 1)[0]
            if name not in mapping:
                return _JsonResp({name: {"error": "not found"}})
            nums = mapping[name]
            return _JsonResp(
                {
                    name: {
                        "episodes": {
                            "sub": [{"number": n} for n in nums],
                            "dub": [],
                        }
                    }
                }
            )
        if head == "watch":
            parts = rest.split("/")
            name = parts[0]
            stream = {
                "url": f"https://cdn/{name}/master.m3u8",
                "type": "hls",
            }
            return _JsonResp({"streams": [stream], "subtitles": []})
        raise AssertionError(f"unexpected get path: {path}")

    monkeypatch.setattr(anivexa, "_get", fake_get)


def _cap_stub(monkeypatch, capability):
    monkeypatch.setattr(anivexa, "_probe_capability", lambda aid, season: capability)


def test_anivexa_episode_probe_is_episode_aware(monkeypatch):
    """episode_capability reads THAT episode's masters, cached per episode."""
    clock = _clock(monkeypatch)
    _cap_stub(monkeypatch, ProviderCapability("anivexa", status="ok", qualities=["720"]))

    # stub episodes: anineko carries the anime
    _stub_episodes(monkeypatch, {"anineko": [1, 2, 3]})
    # stub _probe_heights to record the episode passed
    seen = []
    monkeypatch.setattr(
        anivexa,
        "_probe_heights",
        lambda internal, aid, deadline, episode=1: (
            seen.append(episode) or [720]
        ),
    )

    cap = anivexa.episode_capability(20807, 1, 2)
    assert cap.status == "ok"
    assert cap.qualities == ["720"]
    assert seen == [2]  # episode 2's masters, not episode 1's

    # second call for the SAME episode is served from cache (no re-probe)
    seen.clear()
    anivexa.episode_capability(20807, 1, 2)
    assert seen == []


def test_anivexa_episode_short_circuits_on_season_unavailable(monkeypatch):
    """A season authoritatively UNAVAILABLE short-circuits without probing an
    episode (no watch / height calls)."""
    clock = _clock(monkeypatch)
    _cap_stub(monkeypatch, ProviderCapability("anivexa", status="unavailable"))
    height_calls = []
    monkeypatch.setattr(
        anivexa,
        "_probe_heights",
        lambda *a, **k: height_calls.append(1) or None,
    )

    cap = anivexa.episode_capability(20807, 1, 5)
    assert cap.status == "unavailable"
    assert height_calls == []  # never probed the episode


def test_anivexa_episode_cache_does_not_collide_across_episodes(monkeypatch):
    """The episode cache is keyed (id, season, episode) — revisiting season 1
    ep 1 does not serve season 1 ep 2's cached heights."""
    clock = _clock(monkeypatch)
    mapping = {"anineko": [1, 2, 3]}
    # a probe recorder
    probes = {"n": 0}

    def _probe_cap(aid, season):
        return ProviderCapability("anivexa", status="ok")

    monkeypatch.setattr(anivexa, "_probe_capability", _probe_cap)
    monkeypatch.setattr(
        anivexa,
        "_probe_heights",
        lambda internal, aid, deadline, episode=1: (
            probes.__setitem__("n", probes.get("n", 0) + 1) or [720]
        ),
    )
    _stub_episodes(monkeypatch, mapping)

    anivexa.episode_capability(20807, 1, 1)
    anivexa.episode_capability(20807, 1, 2)
    anivexa.episode_capability(20807, 1, 1)  # cached
    # two distinct episodes were probed, the repeat was cached
    assert probes["n"] == 2


def test_anivexa_episode_verified_cache_expires_after_15min(monkeypatch):
    """A verified per-episode capability is re-probed only after its TTL."""
    clock = _clock(monkeypatch)
    probes = {"n": 0}

    def _probe_cap(aid, season):
        return ProviderCapability("anivexa", status="ok")

    monkeypatch.setattr(anivexa, "_probe_capability", _probe_cap)
    monkeypatch.setattr(
        anivexa,
        "_probe_heights",
        lambda internal, aid, deadline, episode=1: (
            probes.__setitem__("n", probes.get("n", 0) + 1) or [480]
        ),
    )
    _stub_episodes(monkeypatch, {"anineko": [1, 2, 3]})

    anivexa.episode_capability(20807, 1, 1)
    assert probes["n"] == 1
    # inside the 15-minute verified TTL — served from cache
    anivexa.episode_capability(20807, 1, 1)
    assert probes["n"] == 1
    clock.now += 15 * 60 + 1
    anivexa.episode_capability(20807, 1, 1)
    assert probes["n"] == 2  # expired: re-probed


# ── /qualities endpoint semantics ────────────────────────────────────────────

def _call_qualities(monkeypatch, media_id=16498, season=1, episode=1):
    """Return a TestClient for the /qualities endpoint, with the franchise walk
    stubbed to one TV season and (by default) a registry of stub providers."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.anime import anilist

    # franchise walk stubbed with one TV season
    monkeypatch.setattr(
        anilist,
        "franchise",
        lambda mid: [
            anilist.AniMedia(
                id=mid,
                title_romaji="NARUTO",
                title_english=None,
                format="TV",
                episodes=12,
                season_year=2002,
                status="FINISHED",
                cover_url=None,
            )
        ],
    )
    return TestClient(app)


def _get_qualities(client, media_id=16498, season=1, episode=1):
    return client.get(
        f"/api/anime/{media_id}/season/{season}/episode/{episode}/qualities"
    )


def test_qualities_endpoint_returns_all_providers(monkeypatch):
    """Verify anivexa reports its verified list + hianime reports null."""
    # providers registry: anivexa probe returns ok with [480, 720, 1080]
    class OS:
        name = "hianime"
        streams_hls = True

        def available(self):
            return True

    monkeypatch.setattr(
        providers_module,
        "providers",
        lambda: [anivexa.AnivexaProvider(), OS()],
    )
    monkeypatch.setattr(
        anivexa,
        "episode_capability",
        lambda aid, season, episode: ProviderCapability(
            "anivexa", status="ok", qualities=["480", "720", "1080"]
        ),
    )
    resp = _get_qualities(_call_qualities(monkeypatch))
    assert resp.status_code == 200
    provs = resp.json()["providers"]
    anivexa_provider = next(p for p in provs if p["name"] == "anivexa")
    assert anivexa_provider["status"] == "ok"
    assert anivexa_provider["qualities"] == ["480", "720", "1080"]
    hianime_provider = next(p for p in provs if p["name"] == "hianime")
    assert hianime_provider["qualities"] is None  # per-episode, not probed


def test_qualities_endpoint_unknown_never_hides(monkeypatch):
    """A provider that errors degrades to unknown with null qualities."""
    import httpx

    class Boom:
        name = "anivexa"
        streams_hls = True

        def available(self):
            return True

    class OS:
        name = "hianime"
        streams_hls = True

        def available(self):
            return True

    monkeypatch.setattr(providers_module, "providers", lambda: [Boom(), OS()])
    # anivexa probe raises -> the route catches and returns unknown/null
    monkeypatch.setattr(
        anivexa,
        "episode_capability",
        lambda aid, season, episode: (_ for _ in ()).throw(
            httpx.ReadTimeout("timeout")
        ),
    )
    resp = _get_qualities(_call_qualities(monkeypatch))
    assert resp.status_code == 200
    provs = resp.json()["providers"]
    anivexa_provider = next(p for p in provs if p["name"] == "anivexa")
    assert anivexa_provider["status"] == "unknown"
    assert anivexa_provider["qualities"] is None  # not a verdict, never hides


def test_qualities_endpoint_nyaa_empty_is_ok_not_unknown(monkeypatch):
    """Nyaa answering with no seeded release is status ok + empty qualities —
    a genuine 'nothing served right now', not 'couldn't tell'."""
    class Nyaa1:
        name = "nyaa"
        streams_hls = False

        def available(self):
            return True

    class OS:
        name = "hianime"
        streams_hls = True

        def available(self):
            return True

    monkeypatch.setattr(providers_module, "providers", lambda: [Nyaa1(), OS()])
    monkeypatch.setattr(
        nyaa.NyaaProvider,
        "episode_resolutions",
        lambda self, src, ep: [],
    )
    resp = _get_qualities(_call_qualities(monkeypatch))
    assert resp.status_code == 200
    nyaa_provider = next(p for p in resp.json()["providers"] if p["name"] == "nyaa")
    assert nyaa_provider["status"] == "ok"
    assert nyaa_provider["qualities"] == []


def test_qualities_endpoint_bad_input(monkeypatch):
    """Non-positive media_id / season / episode is a clean 400."""
    client = _call_qualities(monkeypatch)
    for path in [
        "/api/anime/0/season/1/episode/1/qualities",
        "/api/anime/16498/season/0/episode/1/qualities",
        "/api/anime/16498/season/1/episode/0/qualities",
    ]:
        resp = client.get(path)
        assert resp.status_code == 400


def test_qualities_endpoint_nyaa_probe_uses_title_not_anilist_id(monkeypatch):
    """Regression: the /qualities route must search Nyaa by the anime's
    *title*, never by the AniList numeric id — Nyaa keys its release search by
    the title, so an id query finds nothing and the whole season's availability
    is reported as served-by-nothing.

    The route builds a synthetic EpisodeSource per provider and calls
    `episode_resolutions(src, episode)`, which queries
    `f"{src.anime_id} S{season:02d}E{episode:02d}"`. The AniList id must stay in
    `anilist_id` (that is what anivexa reads), and must never leak into Nyaa's
    search text."""
    from app.anime.providers import ProviderCapability

    class Nyaa1:
        name = "nyaa"
        streams_hls = False

        def available(self):
            return True

    class OS:
        name = "hianime"
        streams_hls = True

        def available(self):
            return True

    monkeypatch.setattr(
        providers_module,
        "providers",
        lambda: [Nyaa1(), anivexa.AnivexaProvider(), OS()],
    )

    # Fake Nyaa search: serve a 720p release, and record the query asked.
    seen: list[str] = []

    def fake_get(*a, **kwargs):
        seen.append(kwargs.get("params", {}).get("q", ""))
        return _FakeResp(
            _nyaa_page(_nyaa_row(11, "[SubsPlease] NARUTO - 01 (720p)", "a", 42))
        )

    monkeypatch.setattr(nyaa._client, "get", fake_get)

    # anivexa's capability is keyed by the AniList id — record what it got.
    anivexa_ids: list[int] = []

    def fake_capability(aid, season, episode):
        anivexa_ids.append(aid)
        return ProviderCapability("anivexa", status="ok", qualities=["480", "720", "1080"])

    monkeypatch.setattr(anivexa, "episode_capability", fake_capability)

    # Media id 16498, season title NARUTO. If the route searched by id, the
    # Nyaa query would contain "16498".
    resp = _get_qualities(_call_qualities(monkeypatch), media_id=16498, season=1, episode=1)
    assert resp.status_code == 200
    provs = resp.json()["providers"]
    nyaa_provider = next(p for p in provs if p["name"] == "nyaa")
    assert nyaa_provider["status"] == "ok"
    assert nyaa_provider["qualities"] == ["720"]
    anivexa_provider = next(p for p in provs if p["name"] == "anivexa")
    assert anivexa_provider["qualities"] == ["480", "720", "1080"]

    # The Nyaa probe must have searched by the title, never the numeric id.
    # `episode_resolutions` issues two queries: the season-tagged form
    # (NARUTO S01E01) and the bare-number fallback (NARUTO 1) — both must carry
    # the title, neither may carry the AniList id.
    assert seen, "expected Nyaa to be queried for the episode"
    for q in seen:
        assert "16498" not in q, f"Nyaa probed by AniList id instead of title: {q}"
        assert "NARUTO" in q, f"Nyaa probe missing the searchable title: {q}"
        assert any(tag in q for tag in ("S01E01", " 1")), f"Nyaa query missing the episode tag: {q}"

    # anivexa still keys by the id — untouched by the fix.
    assert anivexa_ids == [16498], f"anivexa did not receive the AniList id: {anivexa_ids}"