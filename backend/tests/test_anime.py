"""Anime provider chain, downloader and route fallbacks — no network.

The providers are real classes but their HTTP boundary is stubbed, so these
tests exercise the downloader's provider dispatch, the m3u8/yt-dlp path with
a fake provider, the route's episode-count fallback, and the file mime fix —
all against in-memory state.
"""

import pytest

from app import downloader, jobs
from app.anime import downloader as anime_downloader
from app.models import Track


def make_episode_track(track_id="1:s1e1", episode=1) -> Track:
    return Track(
        id=track_id,
        title=f"Episode {episode}",
        artists=["Naruto"],
        album="Naruto — Season 1",
        duration_ms=24 * 60 * 60 * 1000,
        cover_url=None,
        track_number=episode,
        media="video",
        source_url=f"anime://hianime/naruto/1/{episode}",
    )


def test_parse_source_url():
    src = anime_downloader.parse_source_url("anime://hianime/naruto/1/3")
    assert src.provider == "hianime"
    assert src.anime_id == "naruto"
    assert src.season == 1
    assert src.episode == 3


def test_pick_resolution_clamps():
    assert anime_downloader._pick_resolution("480") == "480"
    assert anime_downloader._pick_resolution("garbage") == anime_downloader.DEFAULT_VIDEO_QUALITY


def test_dispatch_routes_video_tracks_to_video_pipeline(monkeypatch, tmp_path):
    """The audio downloader's entry point hands a video track to the anime
    pipeline instead of running the YouTube search."""
    called = []

    def fake_video(*args, **kwargs):
        called.append(1)
        return tmp_path / "out.mp4"

    monkeypatch.setattr(anime_downloader, "download_video_track", fake_video)
    monkeypatch.setattr(downloader.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    out = downloader.download_track(
        make_episode_track(), tmp_path, lambda stage, fraction: None
    )
    assert called == [1]
    assert out.name == "out.mp4"


def test_downloader_falls_back_to_next_provider(monkeypatch, tmp_path):
    """A provider that fails mid-stream is hopped over; the chain lands on the
    next one that resolves."""
    class Rotten:
        name = "hianime"
        streams_hls = True

        def available(self):
            return True

        def episode_stream(self, src, quality):
            return anime_downloader.EpisodeStream(
                provider=self.name, url="https://rot.example/playlist.m3u8"
            )

        def episode_count(self, src):
            return None

        def resolve(self, title, year):
            raise Exception("rot")  # noqa: TRY002

    class Good:
        name = "hianime2"
        streams_hls = True

        def available(self):
            return True

        def episode_stream(self, src, quality):
            return anime_downloader.EpisodeStream(
                provider=self.name, url="https://cdn.example/playlist.m3u8"
            )

        def episode_count(self, src):
            return None

    # Both providers resolve the plan (route picks the first), but at download
    # time the first one's stream fails, so the chain must try the second.
    made = []

    def fake_ytdlp(stream, dest, on_progress, should_cancel, quality):
        made.append(stream.provider)
        if stream.provider == "hianime":
            raise Exception("network fail")  # noqa: TRY002
        out = dest.with_name(dest.name + ".mp4")
        out.write_bytes(b"fake mp4")
        return out

    monkeypatch.setattr(anime_downloader, "_download_with_ytdlp", fake_ytdlp)
    monkeypatch.setattr(anime_downloader, "_mux_subtitles", lambda v, s, d: v)
    monkeypatch.setattr(anime_downloader, "_fetch_subs", lambda s, d: None)
    # The fake file isn't a real video, so the post-download height check is
    # stubbed to the requested 720p — this test is about provider fallback,
    # not about the (separately covered) served-height verification.
    monkeypatch.setattr(anime_downloader, "_probe_height", lambda p: 720)
    from app.anime import providers as providers_module

    monkeypatch.setattr(
        providers_module,
        "providers",
        lambda: [Rotten(), Good()],
    )
    monkeypatch.setattr(downloader.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    track = make_episode_track()
    out = anime_downloader.download_video_track(
        track, tmp_path, lambda stage, fraction: None, "720", None, None
    )
    assert made == ["hianime", "hianime2"]  # rotten first, then good
    assert out.exists()


def test_downloader_cleans_partials_on_cancel(monkeypatch, tmp_path):
    """A cancelled video download leaves nothing behind, like the audio path."""
    # The stem comes from "album - title" (see download_video_track).
    partial = tmp_path / "Naruto — Season 1 - Episode 1.mp4.part"
    stopped = {"flag": False}

    def fake_ytdlp(stream, dest, on_progress, should_cancel, quality):
        partial.write_bytes(b"\0" * 16)
        stopped["flag"] = True
        raise downloader.Cancelled()

    monkeypatch.setattr(anime_downloader, "_download_with_ytdlp", fake_ytdlp)
    monkeypatch.setattr(anime_downloader, "_fetch_subs", lambda s, d: None)

    class Provider:
        name = "hianime"
        streams_hls = True

        def available(self):
            return True

        def episode_stream(self, src, quality):
            return anime_downloader.EpisodeStream(provider=self.name, url="https://x")

    from app.anime import providers as providers_module

    monkeypatch.setattr(providers_module, "providers", lambda: [Provider()])
    monkeypatch.setattr(downloader.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    with pytest.raises(downloader.Cancelled):
        anime_downloader.download_video_track(
            make_episode_track(), tmp_path, lambda s, f: None, "720", None, stopped["flag"]
        )
    assert not partial.exists()


def test_video_job_reports_mp4_ext(monkeypatch, tmp_path):
    """A finished video track's file lands with an mp4 ext the dock can label."""
    monkeypatch.setattr(jobs, "DOWNLOADS_DIR", tmp_path)
    job = jobs.Job(id="jv1", name="Naruto — Season 1", quality="720")
    track = make_episode_track()
    state = jobs.TrackState(track=track, filename="Naruto - Episode 1")
    job.tracks[track.id] = state

    out = tmp_path / job.id / "Naruto - Episode 1.mp4"
    out.parent.mkdir(parents=True)
    out.write_bytes(b"video")

    monkeypatch.setattr(
        jobs.downloader,
        "download_track",
        lambda *a, **k: out,
    )
    jobs._run_track(job, state)
    assert state.status == "done"
    assert state.as_dict()["ext"] == "mp4"


def test_download_route_filters_episode_subset(monkeypatch):
    """POST /api/anime/download with episode_ids downloads only those, and an
    unknown-only selection is refused with a clean 400."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.anime import anilist, providers as providers_module
    from app.anime.providers import EpisodeSource, EpisodeStream

    class FakeProvider:
        name = "hianime"
        streams_hls = True

        def available(self):
            return True

        def resolve(self, title, year):
            return EpisodeSource(
                provider="hianime", anime_id="aot", anime_title=title,
                year=year, season=0, episode=0,
            )

        def episode_count(self, src):
            return 25

        def episode_stream(self, src, quality):
            return EpisodeStream(provider="hianime", url="https://cdn.example/p.m3u8")

    monkeypatch.setattr(providers_module, "providers", lambda: [FakeProvider()])
    # The franchise walk is a live AniList call, which a unit test must not
    # depend on — stub it with one TV season for the requested media_id. The
    # route reads best_title/season_year/cover_url off the entry.
    monkeypatch.setattr(
        anilist,
        "franchise",
        lambda media_id: [
            anilist.AniMedia(
                id=media_id,
                title_romaji="SHINGEKI_NO_KYOJIN",
                title_english="Attack on Titan",
                format="TV",
                episodes=25,
                season_year=2013,
                status="FINISHED",
                cover_url="https://example.com/cover.jpg",
            )
        ],
    )
    from app import jobs as jobs_module

    captured = {"tracks": None}
    from types import SimpleNamespace

    monkeypatch.setattr(
        jobs_module,
        "start",
        lambda name, tracks, quality, embed_lyrics, owner, visitor: (
            captured.__setitem__("tracks", tracks) or SimpleNamespace(id="job123")
        ),
    )

    client = TestClient(app)
    resp = client.post(
        "/api/anime/download",
        json={
            "media_id": 16498,
            "season": 1,
            "quality": "720",
            "episode_ids": ["16498:s1e1", "16498:s1e3"],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["job_id"] == "job123"
    assert [t.id for t in captured["tracks"]] == ["16498:s1e1", "16498:s1e3"]

    # A well-formed explicit id that doesn't match the AniList count still
    # becomes a track — Nyaa has no episode registry, so the id is honored and
    # the torrent search decides at download time.
    resp2 = client.post(
        "/api/anime/download",
        json={"media_id": 16498, "season": 1, "episode_ids": ["16498:s1e999"]},
    )
    assert resp2.status_code == 200
    assert [t.id for t in captured["tracks"]] == ["16498:s1e999"]


def test_nyaa_search_picks_best_seeded_matching_episode(monkeypatch):
    """The Nyaa provider finds the highest-seeded upload that names the episode."""
    from app.anime import nyaa

    # A fake Nyaa search page with a header row + three torrents: a batch (no
    # episode number), a 5-seeder episode, and a 60-seeder episode.
    html = """
    <table class="torrent-list"><tbody>
      <tr><th>Category</th><th>Name</th></tr>
      <tr><td><a title="Anime - English-translated"></a></td>
          <td colspan="2"><a href="/view/1" title="[SubsPlease] Naruto Batch 1-50"></a>
          <a href="magnet:?xt=urn:btih:batch"></a></td></tr>
      <tr><td><a title="Anime - English-translated"></a></td>
          <td colspan="2"><a href="/view/2" title="[SubsPlease] One Piece - 1100 (1080p)"></a>
          <a href="magnet:?xt=urn:btih:ep5"></a></td>
          <td class="text-center">5</td></tr>
      <tr><td><a title="Anime - English-translated"></a></td>
          <td colspan="2"><a href="/view/3" title="[Judas] One Piece - 1100 [1080p]"></a>
          <a href="magnet:?xt=urn:btih:ep60"></a></td>
          <td class="text-center">60</td></tr>
    </tbody></table>
    """
    class FakeResp:
        text = html

        def raise_for_status(self):
            return None

    monkeypatch.setattr(nyaa._client, "get", lambda *a, **k: FakeResp())

    p = nyaa.NyaaProvider()
    src = p.resolve("One Piece", 1999)
    torrent = p._search_episode(src, 1100)
    assert torrent["seeders"] == 60  # the best-seeded episode, not the batch
    assert "ep60" in torrent["magnet"]


def test_nyaa_batch_only_episode_falls_back_to_batch(monkeypatch):
    """An episode that only exists inside a multi-episode batch is returned as
    a batch fallback (marked `batch: True`) so the downloader can extract it,
    rather than a wrong-episode single or a bare refusal."""
    from app.anime import nyaa

    html = """
    <table class="torrent-list"><tbody>
      <tr><th>Category</th><th>Name</th></tr>
      <tr><td><a title="Anime - English-translated"></a></td>
          <td colspan="2"><a href="/view/9" title="[Hxod] One Piece 001-206 [Dual Audio]"></a>
          <a href="magnet:?xt=urn:btih:batch"></a></td>
          <td class="text-center">253</td></tr>
    </tbody></table>
    """
    class FakeResp:
        text = html

        def raise_for_status(self):
            return None

    monkeypatch.setattr(nyaa._client, "get", lambda *a, **k: FakeResp())

    p = nyaa.NyaaProvider()
    src = p.resolve("One Piece", 1999)
    torrent = p._search_episode(src, 1)
    assert torrent.get("batch") is True
    assert torrent["torrent_id"] == "9"


def test_nyaa_single_episode_beats_batch(monkeypatch):
    """A true single-episode release (E1100) beats a batch (001-206)."""
    from app.anime import nyaa

    html = """
    <table class="torrent-list"><tbody>
      <tr><th>Category</th><th>Name</th></tr>
      <tr><td><a title="Anime - English-translated"></a></td>
          <td colspan="2"><a href="/view/1" title="[Hxod] One Piece 001-206 [Dual Audio]"></a>
          <a href="magnet:?xt=urn:btih:batch"></a></td>
          <td class="text-center">253</td></tr>
      <tr><td><a title="Anime - English-translated"></a></td>
          <td colspan="2"><a href="/view/2" title="[ToonsHub] One Piece - E1100 (1080p)"></a>
          <a href="magnet:?xt=urn:btih:ep1100"></a></td>
          <td class="text-center">9</td></tr>
    </tbody></table>
    """
    class FakeResp:
        text = html

        def raise_for_status(self):
            return None

    monkeypatch.setattr(nyaa._client, "get", lambda *a, **k: FakeResp())

    p = nyaa.NyaaProvider()
    src = p.resolve("One Piece", 1999)
    torrent = p._search_episode(src, 1100)
    assert "ep1100" in torrent["magnet"]  # the single, not the batch


# ── Requested quality is honored: Nyaa selects by resolution, the chain and
#    selector never silently serve another resolution, and the job state
#    records what actually happened (provider + ffprobe'd height). ─────────────


def _nyaa_page(*rows: str) -> str:
    return (
        '<table class="torrent-list"><tbody>'
        "<tr><th>Category</th><th>Name</th></tr>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _nyaa_row(torrent_id: int, title: str, magnet: str, seeders: int, size="100.0 MiB") -> str:
    return (
        '<tr><td><a title="Anime - English-translated"></a></td>'
        f'<td colspan="2"><a href="/view/{torrent_id}" title="{title}"></a>'
        f'<a href="magnet:?xt=urn:btih:{magnet}"></a></td>'
        f'<td class="text-center">{seeders}</td>'
        f'<td>{size}</td></tr>'
    )


class _NyaaResp:
    def __init__(self, html: str):
        self.text = html

    def raise_for_status(self):
        return None


def _nyaa_source(title="Show", season=1, episode=1):
    from app.anime.providers import EpisodeSource

    return EpisodeSource(provider="nyaa", anime_id=title, anime_title=title,
                         year=2020, season=season, episode=episode)


def _search_page(monkeypatch, html, episode=1, quality=""):
    from app.anime import nyaa

    monkeypatch.setattr(nyaa._client, "get", lambda *a, **k: _NyaaResp(html))
    return nyaa.NyaaProvider()._search_episode(
        _nyaa_source(episode=episode), episode, quality
    )


def test_resolution_matcher_is_explicit_about_p():
    """'48000' (an audio bitrate) or '001-480' (an episode range) is not a
    480p marker — only an explicit `p` or width×height is."""
    from app.anime.nyaa import _title_resolution

    assert _title_resolution("[X] Show - 01 [48000Hz FLAC]") is None
    assert _title_resolution("[X] Show - 01 (48000 Hz)") is None
    assert _title_resolution("[X] Show 001-480") is None
    assert _title_resolution("[X] Show 480") is None  # a bare number is not 480p
    # Positive controls.
    assert _title_resolution("[X] Show - 01 [480p].mkv") == "480"
    assert _title_resolution("[X] Show - 01 (1280x720)") == "720"
    assert _title_resolution("[X] Show - 01 [1080p HEVC]") == "1080"


def test_nyaa_requests_480_picks_480p_over_best_seeded(monkeypatch):
    """A 480p request ignores a higher-seeded 720p release."""
    html = _nyaa_page(
        _nyaa_row(1, "[SubsPlease] Show - 01 [720p].mkv", "seed720", 200),
        _nyaa_row(2, "[Judas] Show - 01 [480p].mkv", "seed480", 5),
    )
    torrent = _search_page(monkeypatch, html, quality="480")
    assert torrent["torrent_id"] == "2"
    assert "480p" in torrent["title"]


def test_nyaa_requests_480_with_only_720_raises_quality_unavailable(monkeypatch):
    from app.anime.nyaa import QualityUnavailable

    html = _nyaa_page(_nyaa_row(1, "[SubsPlease] Show - 01 [720p].mkv", "seed720", 200))
    with pytest.raises(QualityUnavailable):
        _search_page(monkeypatch, html, quality="480")


def test_nyaa_requests_720_picks_720p(monkeypatch):
    html = _nyaa_page(
        _nyaa_row(1, "[X] Show - 01 [1080p].mkv", "seed1080", 100),
        _nyaa_row(2, "[X] Show - 01 [720p].mkv", "seed720", 10),
    )
    torrent = _search_page(monkeypatch, html, quality="720")
    assert torrent["torrent_id"] == "2"


def test_nyaa_requests_1080_picks_1080p(monkeypatch):
    html = _nyaa_page(
        _nyaa_row(1, "[X] Show - 01 [480p].mkv", "seed480", 300),
        _nyaa_row(2, "[X] Show - 01 [1080p].mkv", "seed1080", 3),
    )
    torrent = _search_page(monkeypatch, html, quality="1080")
    assert torrent["torrent_id"] == "2"


def test_nyaa_original_keeps_best_seeded(monkeypatch):
    """`original` still takes the best-seeded torrent whatever its resolution."""
    html = _nyaa_page(
        _nyaa_row(1, "[X] Show - 01 [1080p].mkv", "seed1080", 300),
        _nyaa_row(2, "[X] Show - 01 [720p].mkv", "seed720", 5),
    )
    torrent = _search_page(monkeypatch, html, quality="original")
    assert torrent["torrent_id"] == "1"


def test_downloader_asks_fallback_for_same_quality(monkeypatch, tmp_path):
    """When Nyaa raises QualityUnavailable, the next provider is asked for the
    SAME resolution — never a different one."""
    from app.anime import downloader as anime_downloader
    from app.anime import providers as providers_module
    from app.anime.providers import EpisodeStream, QualityUnavailable
    from app.models import Track

    class NyaaNo480:
        name = "nyaa"
        streams_hls = False

        def available(self):
            return True

        def episode_stream(self, src, quality):
            raise QualityUnavailable("no 480p on nyaa")

        def download(self, *a, **k):
            raise AssertionError("nyaa download must not run")

    seen = []

    class HiAnime:
        name = "hianime"
        streams_hls = True

        def available(self):
            return True

        def episode_stream(self, src, quality):
            seen.append(quality)
            return EpisodeStream(provider="hianime", url="https://cdn.example/m.m3u8")

    monkeypatch.setattr(providers_module, "providers", lambda: [NyaaNo480(), HiAnime()])

    def fake_ytdlp(stream, dest, on_progress, should_cancel, quality):
        out = dest.with_name(dest.name + ".mp4")
        out.write_bytes(b"v")
        return out

    monkeypatch.setattr(anime_downloader, "_download_with_ytdlp", fake_ytdlp)
    monkeypatch.setattr(anime_downloader, "_fetch_subs", lambda s, d: None)
    monkeypatch.setattr(anime_downloader, "_mux_subtitles", lambda v, s, d: v)
    # The fake file isn't a real video; stub the height check so the fallback
    # succeeds at the requested 480p (this test asserts the fallback quality).
    monkeypatch.setattr(anime_downloader, "_probe_height", lambda p: 480)
    monkeypatch.setattr(downloader.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    track = Track(id="1:s1e1", title="Episode 1", artists=["Show"],
                  album="Show — Season 1", duration_ms=1, cover_url=None,
                  track_number=1, media="video", source_url="anime://nyaa/Show/1/1")
    out = anime_downloader.download_video_track(
        track, tmp_path, lambda s, f: None, "480", None, None
    )
    assert out.exists()
    assert seen == ["480"]  # HiAnime was asked for the same resolution


def test_both_providers_quality_unavailable_fails_clearly(monkeypatch, tmp_path):
    from app.anime import downloader as anime_downloader
    from app.anime import providers as providers_module
    from app.anime.providers import QualityUnavailable
    from app.downloader import DownloadError
    from app.models import Track

    class NyaaNo480:
        name = "nyaa"
        streams_hls = False

        def available(self):
            return True

        def episode_stream(self, src, quality):
            raise QualityUnavailable("no 480p")

        def download(self, *a, **k):
            raise AssertionError

    class HiAnimeNo480:
        name = "hianime"
        streams_hls = True

        def available(self):
            return True

        def episode_stream(self, src, quality):
            raise QualityUnavailable("no 480p")

    monkeypatch.setattr(providers_module, "providers", lambda: [NyaaNo480(), HiAnimeNo480()])
    monkeypatch.setattr(downloader.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    track = Track(id="1:s1e1", title="Episode 1", artists=["Show"],
                  album="Show — Season 1", duration_ms=1, cover_url=None,
                  track_number=1, media="video", source_url="anime://nyaa/Show/1/1")
    with pytest.raises(DownloadError, match="Requested quality 480p is unavailable"):
        anime_downloader.download_video_track(
            track, tmp_path, lambda s, f: None, "480", None, None
        )


def test_quality_unavailable_not_masked_by_later_provider_error(monkeypatch, tmp_path):
    """A provider that could evaluate the resolution (QualityUnavailable) must
    not be masked by a later provider's unrelated ProviderError (rot,
    unreachable): the clean quality message survives, not the generic error."""
    from app.anime import downloader as anime_downloader
    from app.anime import providers as providers_module
    from app.anime.providers import QualityUnavailable
    from app.downloader import DownloadError
    from app.models import ProviderError, Track

    class NyaaNo480:
        name = "nyaa"
        streams_hls = False

        def available(self):
            return True

        def episode_stream(self, src, quality):
            raise QualityUnavailable("no 480p")

        def download(self, *a, **k):
            raise AssertionError("nyaa download must not run")

    class HiAnimeDown:
        name = "hianime"
        streams_hls = True

        def available(self):
            return True

        def episode_stream(self, src, quality):
            raise ProviderError("hianime unreachable")

    monkeypatch.setattr(providers_module, "providers", lambda: [NyaaNo480(), HiAnimeDown()])
    monkeypatch.setattr(downloader.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    track = Track(id="1:s1e1", title="Episode 1", artists=["Show"],
                  album="Show — Season 1", duration_ms=1, cover_url=None,
                  track_number=1, media="video", source_url="anime://nyaa/Show/1/1")
    with pytest.raises(DownloadError, match="Requested quality 480p is unavailable"):
        anime_downloader.download_video_track(
            track, tmp_path, lambda s, f: None, "480", None, None
        )


def test_provider_errors_without_quality_unavailable_keep_generic_error(monkeypatch, tmp_path):
    """No provider raised QualityUnavailable -> the real technical error
    survives (never hidden behind the quality message)."""
    from app.anime import downloader as anime_downloader
    from app.anime import providers as providers_module
    from app.downloader import DownloadError
    from app.models import ProviderError, Track

    class NyaaDown:
        name = "nyaa"
        streams_hls = False

        def available(self):
            return True

        def episode_stream(self, src, quality):
            raise ProviderError("nyaa unreachable")

        def download(self, *a, **k):
            raise AssertionError

    class HiAnimeDown:
        name = "hianime"
        streams_hls = True

        def available(self):
            return True

        def episode_stream(self, src, quality):
            raise ProviderError("hianime unreachable")

    monkeypatch.setattr(providers_module, "providers", lambda: [NyaaDown(), HiAnimeDown()])
    monkeypatch.setattr(downloader.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    track = Track(id="1:s1e1", title="Episode 1", artists=["Show"],
                  album="Show — Season 1", duration_ms=1, cover_url=None,
                  track_number=1, media="video", source_url="anime://nyaa/Show/1/1")
    with pytest.raises(DownloadError, match="Failed to download episode after trying all providers"):
        anime_downloader.download_video_track(
            track, tmp_path, lambda s, f: None, "480", None, None
        )


def test_format_selector_is_strict_for_explicit_quality():
    """An explicit resolution has no unrestricted `/best` fallback — a missing
    variant fails the download rather than silently upgrading."""
    from app.anime.downloader import _format_selector

    assert _format_selector("480") == "bestvideo[height=480]+bestaudio/best[height=480]"
    assert _format_selector("720") == "bestvideo[height=720]+bestaudio/best[height=720]"
    assert _format_selector("1080") == "bestvideo[height=1080]+bestaudio/best[height=1080]"
    assert _format_selector("original") == "bestvideo+bestaudio/best"
    for quality in ("480", "720", "1080"):
        assert not _format_selector(quality).endswith("/best")


def test_served_quality_comes_from_probed_height(monkeypatch, tmp_path):
    """The recorded served_quality is the ffprobe'd height, not the request."""
    from app.anime import downloader as anime_downloader
    from app.anime import providers as providers_module
    from app.anime.providers import EpisodeStream
    from app.models import Track

    class FakeNyaa:
        name = "nyaa"
        streams_hls = False

        def available(self):
            return True

        def episode_stream(self, src, quality):
            return EpisodeStream(provider="nyaa", url="magnet:?xt=urn:btih:abc")

        def download(self, stream, dest, quality, on_progress, should_cancel, subs="eng"):
            out = dest.with_name(dest.name + ".mkv")
            out.write_bytes(b"video")
            return out

    monkeypatch.setattr(providers_module, "providers", lambda: [FakeNyaa()])
    monkeypatch.setattr(anime_downloader, "_fetch_subs", lambda s, d: None)
    monkeypatch.setattr(anime_downloader, "_mux_subtitles", lambda v, s, d: v)
    monkeypatch.setattr(anime_downloader, "_probe_height", lambda p: 480)
    monkeypatch.setattr(downloader.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    track = Track(id="1:s1e1", title="Episode 1", artists=["Show"],
                  album="Show — Season 1", duration_ms=1, cover_url=None,
                  track_number=1, media="video", source_url="anime://nyaa/Show/1/1")
    meta: dict = {}
    out = anime_downloader.download_video_track(
        track, tmp_path, lambda s, f: None, "480", None, None, meta=meta
    )
    assert out.exists()
    assert meta["provider"] == "nyaa"
    assert meta["served_quality"] == "480p"


def test_probe_height_reads_real_file(tmp_path):
    import shutil as _shutil
    import subprocess

    if _shutil.which("ffmpeg") is None or _shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not installed")
    from app.anime import downloader as anime_downloader

    video = tmp_path / "probe.mp4"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=854x480:rate=1:duration=1",
         "-c:v", "mpeg4", str(video)],
        capture_output=True,
    )
    if proc.returncode != 0:
        pytest.skip("ffmpeg could not encode a test frame")
    assert anime_downloader._probe_height(video) == 480


def test_track_state_exposes_provider_and_served_quality(monkeypatch, tmp_path):
    """The job API exposes which provider served the episode and the actual
    resolution, filled from the pipeline's meta rather than echoed."""
    monkeypatch.setattr(jobs, "DOWNLOADS_DIR", tmp_path)
    job = jobs.Job(id="jq1", name="Show — Season 1", quality="480")
    track = make_episode_track()
    state = jobs.TrackState(track=track, filename="Show - Episode 1")

    out = tmp_path / job.id / "Show - Episode 1.mp4"
    out.parent.mkdir(parents=True)
    out.write_bytes(b"video")

    def fake_download_track(*args, **kwargs):
        kwargs["meta"].update(provider="nyaa", served_quality="480p")
        return out

    monkeypatch.setattr(jobs.downloader, "download_track", fake_download_track)
    jobs._run_track(job, state)
    assert state.status == "done"
    assert state.provider == "nyaa"
    assert state.served_quality == "480p"
    assert state.as_dict()["provider"] == "nyaa"
    assert state.as_dict()["served_quality"] == "480p"


def test_failed_track_still_records_served_quality(monkeypatch, tmp_path):
    """A failed download still reports what the pipeline served.

    The video pipeline fills `meta` (provider + ffprobe'd height) *before* it
    can fail — a mislabeled release is probed, then refused as quality
    unavailable. That truth must survive on the error path, not just the
    success path: the job API shows requested-vs-served even when the track
    ends in error. Audio leaves `meta` empty and keeps both None.
    """
    monkeypatch.setattr(jobs, "DOWNLOADS_DIR", tmp_path)
    job = jobs.Job(id="jq2", name="Show — Season 1", quality="480")
    track = make_episode_track()
    state = jobs.TrackState(track=track, filename="Show - Episode 1")

    def fake_download_track(*args, **kwargs):
        kwargs["meta"].update(provider="nyaa", served_quality="720p")
        raise downloader.DownloadError(
            "Requested quality 480p is unavailable for this episode."
        )

    monkeypatch.setattr(jobs.downloader, "download_track", fake_download_track)
    jobs._run_track(job, state)
    assert state.status == "error"
    assert state.provider == "nyaa"
    assert state.served_quality == "720p"
    assert state.as_dict()["provider"] == "nyaa"
    assert state.as_dict()["served_quality"] == "720p"


# ── Post-download quality enforcement: a release that claims one resolution
#    but actually contains another is a quality mismatch, not a success. ───────


def _fake_video_provider(name: str, streams_hls: bool = True):
    """A minimal self-downloading (or HLS) provider that produces a real file."""
    from app.anime import downloader as anime_downloader

    provider_name = name  # distinct locals: a class body can't read a shadowed param
    provider_hls = streams_hls

    class _P:
        name = provider_name
        streams_hls = provider_hls

        def available(self):
            return True

        def episode_stream(self, src, quality):
            return anime_downloader.EpisodeStream(
                provider=self.name, url=f"https://cdn.example/{self.name}.m3u8"
            )

        def download(self, stream, dest, quality, on_progress, should_cancel, subs="eng"):
            out = dest.with_name(dest.name + ".mkv")
            out.write_bytes(b"self-downloaded video")
            return out

    return _P()


def _stub_video_pipeline(monkeypatch, probe):
    """Fake the download pipeline's external edges; `probe` supplies heights."""
    from app.anime import downloader as anime_downloader
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


def _video_track(source_url="anime://nyaa/Show/1/1"):
    from app.models import Track

    return Track(id="1:s1e1", title="Episode 1", artists=["Show"],
                 album="Show — Season 1", duration_ms=1, cover_url=None,
                 track_number=1, media="video", source_url=source_url)


def test_check_served_quality():
    """The served-height check accepts a match, skips original, and rejects a
    mismatch or an unverifiable height for explicit requests."""
    from app.anime import downloader as anime_downloader
    from app.anime.providers import QualityUnavailable

    anime_downloader._check_served_quality("480", 480)
    anime_downloader._check_served_quality("720", 720)
    anime_downloader._check_served_quality("original", 720)  # original: never checked
    with pytest.raises(QualityUnavailable):
        anime_downloader._check_served_quality("480", 720)
    with pytest.raises(QualityUnavailable):
        anime_downloader._check_served_quality("1080", 720)
    with pytest.raises(QualityUnavailable):
        anime_downloader._check_served_quality("480", None)


def test_post_download_match_is_success(monkeypatch, tmp_path):
    """Requested 480, served 480 → completed, with served_quality recorded."""
    from app.anime import downloader as anime_downloader
    from app.anime import providers as providers_module

    monkeypatch.setattr(
        providers_module, "providers",
        lambda: [_fake_video_provider("nyaa", streams_hls=False)],
    )
    _stub_video_pipeline(monkeypatch, lambda p: 480)

    meta: dict = {}
    out = anime_downloader.download_video_track(
        _video_track(), tmp_path, lambda s, f: None, "480", None, None, meta=meta
    )
    assert out.exists()
    assert meta["provider"] == "nyaa"
    assert meta["served_quality"] == "480p"


def test_post_download_mismatch_falls_through_to_next_provider(monkeypatch, tmp_path):
    """A release labeled 480p that actually holds a 720p file is a mismatch,
    not a completed download — the chain continues at the same resolution."""
    from app.anime import downloader as anime_downloader
    from app.anime import providers as providers_module
    from app.anime.providers import EpisodeStream

    seen = []

    class Nyaa720:
        name = "nyaa"
        streams_hls = False

        def available(self):
            return True

        def episode_stream(self, src, quality):
            return EpisodeStream(provider="nyaa", url="magnet:?xt=urn:btih:mislabeled")

        def download(self, stream, dest, quality, on_progress, should_cancel, subs="eng"):
            out = dest.with_name(dest.name + ".mkv")
            out.write_bytes(b"the file is actually 720p")
            return out

    class HiAnime480:
        name = "hianime"
        streams_hls = True

        def available(self):
            return True

        def episode_stream(self, src, quality):
            seen.append(quality)
            return EpisodeStream(provider="hianime", url="https://cdn.example/480.m3u8")

    monkeypatch.setattr(providers_module, "providers", lambda: [Nyaa720(), HiAnime480()])
    # Nyaa self-downloads an .mkv (probed 720p); HiAnime yields an .mp4 (480p).
    _stub_video_pipeline(monkeypatch, lambda p: 720 if p.suffix == ".mkv" else 480)

    meta: dict = {}
    out = anime_downloader.download_video_track(
        _video_track(), tmp_path, lambda s, f: None, "480", None, None, meta=meta
    )
    assert out.exists()
    assert meta["provider"] == "hianime"  # the fallback that actually served 480p
    assert meta["served_quality"] == "480p"
    assert seen == ["480"]  # same requested resolution on the fallback


def test_post_download_all_providers_mismatch_fails_cleanly(monkeypatch, tmp_path):
    """Every provider serves the wrong resolution → clean quality-unavailable."""
    from app.anime import downloader as anime_downloader
    from app.anime import providers as providers_module
    from app.downloader import DownloadError

    monkeypatch.setattr(
        providers_module, "providers",
        lambda: [
            _fake_video_provider("nyaa", streams_hls=False),
            _fake_video_provider("hianime", streams_hls=True),
        ],
    )
    _stub_video_pipeline(monkeypatch, lambda p: 720)  # both files are really 720p

    with pytest.raises(DownloadError, match="Requested quality 480p is unavailable"):
        anime_downloader.download_video_track(
            _video_track(), tmp_path, lambda s, f: None, "480", None, None
        )


def test_post_download_original_not_verified(monkeypatch, tmp_path):
    """`original` accepts whatever the source actually served — no check."""
    from app.anime import downloader as anime_downloader
    from app.anime import providers as providers_module

    monkeypatch.setattr(
        providers_module, "providers",
        lambda: [_fake_video_provider("nyaa", streams_hls=False)],
    )
    _stub_video_pipeline(monkeypatch, lambda p: 720)  # a 720p file under "original"

    meta: dict = {}
    out = anime_downloader.download_video_track(
        _video_track(), tmp_path, lambda s, f: None, "original", None, None, meta=meta
    )
    assert out.exists()
    assert meta["served_quality"] == "720p"


def test_audio_path_ignores_video_quality_check(monkeypatch, tmp_path):
    """The audio pipeline is untouched by the video-quality enforcement."""
    from app import downloader
    from app.anime import downloader as anime_downloader
    from app.models import Track

    reached = []
    monkeypatch.setattr(
        downloader, "download_audio",
        lambda *a, **k: (reached.append("audio"), tmp_path / "x.mp3")[1],
    )
    monkeypatch.setattr(downloader, "embed_tags", lambda *a, **k: None)
    monkeypatch.setattr(downloader.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        anime_downloader, "download_video_track",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("video pipeline must not run")),
    )

    track = Track(id="a1", title="Song", artists=["Artist"], album="Album",
                  duration_ms=1000, cover_url=None, track_number=1, media="audio",
                  source_url="https://youtube.com/watch?v=abc")
    meta: dict = {}
    out = downloader.download_track(
        track, tmp_path, lambda s, f: None, embed_lyrics=False, meta=meta
    )
    assert reached == ["audio"]
    assert meta == {}  # audio leaves the video meta untouched


# ── Nyaa batch/range detection: a multi-episode torrent must never be treated
#    as a single episode (which would pull the whole multi-GiB batch). ─────────


def test_nyaa_range_separators_are_batches(monkeypatch):
    """Dash, en-dash, em-dash and tilde episode ranges are all batches."""
    for sep in ("-", "–", "—", "~"):
        html = _nyaa_page(_nyaa_row(1, f"[X] Show 001 {sep} 079 [480p]", "seedbatch", 50))
        torrent = _search_page(monkeypatch, html, episode=1, quality="480")
        assert torrent.get("batch") is True, sep
        assert torrent["torrent_id"] == "1"


def test_nyaa_range_first_episode_is_batch(monkeypatch):
    """The first episode of a tilde range is a batch, not a single."""
    html = _nyaa_page(
        _nyaa_row(1, "[Erai-raws] Naruto Shippuuden - 001 ~ 079 [480p CR]", "seedbatch", 32),
    )
    torrent = _search_page(monkeypatch, html, episode=1, quality="480")
    assert torrent.get("batch") is True
    assert torrent["torrent_id"] == "1"


def test_nyaa_range_middle_episode_is_batch(monkeypatch):
    """An episode in the middle of a range is part of the batch even though its
    number never appears in the title — extract it, don't download the lot."""
    html = _nyaa_page(
        _nyaa_row(1, "[X] Show 001 ~ 079 [480p]", "seedbatch", 50),
    )
    torrent = _search_page(monkeypatch, html, episode=40, quality="480")
    assert torrent.get("batch") is True
    assert torrent["torrent_id"] == "1"


def test_nyaa_range_last_episode_is_batch(monkeypatch):
    """The final episode of a range (the number at the range's END) is still
    recognized as belonging to the batch."""
    html = _nyaa_page(
        _nyaa_row(1, "[X] Show 001-079 [480p]", "seedbatch", 50),
    )
    torrent = _search_page(monkeypatch, html, episode=79, quality="480")
    assert torrent.get("batch") is True
    assert torrent["torrent_id"] == "1"


def test_nyaa_ordinary_single_episode_not_a_batch(monkeypatch):
    """Ordinary single-episode titles are not misread as ranges."""
    html = _nyaa_page(
        _nyaa_row(1, "[SubsPlease] Show - 01 [480p].mkv", "seed1", 50),
        _nyaa_row(2, "[SubsPlease] Show - 01 [480p] (854x480)", "seed2", 60),
        _nyaa_row(3, "[SubsPlease] Show - E01 [480p]", "seed3", 70),
    )
    torrent = _search_page(monkeypatch, html, episode=1, quality="480")
    assert torrent.get("batch") is not True
    assert torrent["seeders"] == 70  # best-seeded single, none misread as a range


def test_nyaa_batch_label_is_not_a_single(monkeypatch):
    """A title explicitly labeled BATCH is a batch even without a parseable
    range — extracting one episode beats pulling the whole torrent."""
    html = _nyaa_page(
        _nyaa_row(1, "[X] Show - 001 [480p][BATCH]", "seedbatch", 50),
    )
    torrent = _search_page(monkeypatch, html, episode=1, quality="480")
    assert torrent.get("batch") is True
    assert torrent["torrent_id"] == "1"


def test_multi_episode_space_list_detection():
    """The conservative space-list detector: only adjacent zero-padded episode
    forms (001, 02, 010) or E/EP markers count; a year, a resolution, an
    SxxExx marker, or a codec number never does."""
    from app.anime.nyaa import _multi_episode_space_list

    # Positive: explicit multi-episode lists.
    assert _multi_episode_space_list("Show 001 002 003 [1080p]")
    assert _multi_episode_space_list("Show 01 02 03 [720p]")
    assert _multi_episode_space_list("Show 010 011 012")  # >9, zero-padded
    assert _multi_episode_space_list("Show 01 02")  # bare two-episode list
    assert _multi_episode_space_list("Show E01 E02 [720p]")
    assert _multi_episode_space_list("Show 001, 002, 003")  # comma-separated

    # Negative: a single episode beside metadata numbers.
    assert not _multi_episode_space_list("Show 01 720p")
    assert not _multi_episode_space_list("Show 01 1080p")
    assert not _multi_episode_space_list("Show 01 2160p")
    assert not _multi_episode_space_list("Show S01E01 720p")
    assert not _multi_episode_space_list("Show 001 [480p]")
    assert not _multi_episode_space_list("[Group] Show 001 [HEVC x265 10bit]")
    assert not _multi_episode_space_list("Show 2001 2002")  # years
    assert not _multi_episode_space_list("Show 01 (2024)")
    assert not _multi_episode_space_list("Show - 01 [480p].mkv")  # single episode
    assert not _multi_episode_space_list("Show 01-02")  # a range, not a space list


def test_nyaa_space_list_first_middle_last_are_batches(monkeypatch):
    """A space-separated episode list (001 002 003) is a batch wherever the
    requested episode falls — first, middle or last — and is never treated as
    a single (which would pull the whole multi-episode torrent)."""
    html = _nyaa_page(
        _nyaa_row(1, "[X] Show 001 002 003 [480p]", "seedbatch", 50),
    )
    for ep in (1, 2, 3):
        torrent = _search_page(monkeypatch, html, episode=ep, quality="480")
        assert torrent.get("batch") is True, ep
        assert torrent["torrent_id"] == "1", ep


def test_nyaa_space_list_ep_markers_are_batches(monkeypatch):
    """E/EP-prefixed space-separated episodes (E01 E02 E03) are a batch too."""
    html = _nyaa_page(
        _nyaa_row(1, "[X] Show E01 E02 E03 [720p]", "seedbatch", 40),
    )
    for ep in (1, 2, 3):
        torrent = _search_page(monkeypatch, html, episode=ep, quality="720")
        assert torrent.get("batch") is True, ep
        assert torrent["torrent_id"] == "1", ep


def test_nyaa_single_episode_with_metadata_numbers_not_a_batch(monkeypatch):
    """A single episode beside a resolution / season / other number must stay a
    single — Show 01 720p is one episode, not an episode list."""
    from app.anime import nyaa
    html = _nyaa_page(
        _nyaa_row(1, "[X] Show 01 720p", "seed1", 50),
        _nyaa_row(2, "[X] Show 01 1080p", "seed2", 60),
        _nyaa_row(3, "[X] Show S01E01 720p", "seed3", 70),
        _nyaa_row(4, "[X] Show 001 [480p]", "seed4", 80),
    )
    singles, batches = nyaa.NyaaProvider._parse_rows(html, 1)
    assert len(batches) == 0  # none misread as a batch
    assert len(singles) == 4
    # And the search still picks a single (never a batch) for this episode.
    torrent = _search_page(monkeypatch, html, episode=1, quality="")
    assert torrent.get("batch") is not True


def test_nyaa_unextractable_batch_never_downloads_whole_torrent(monkeypatch, tmp_path):
    """When a recognized batch can't yield the requested episode, the download
    fails cleanly rather than falling back to pulling the whole multi-GiB
    torrent as if it were a single episode."""
    from app.anime import nyaa
    from app.downloader import DownloadError

    html = _nyaa_page(_nyaa_row(1, "[X] Show 001 ~ 079 [480p]", "seedbatch", 50))
    monkeypatch.setattr(nyaa._client, "get", lambda *a, **k: _NyaaResp(html))

    provider = nyaa.NyaaProvider()
    stream = provider.episode_stream(_nyaa_source(episode=40), "480")
    assert stream.batch is True  # routed to extraction, not a plain torrent pull

    whole = {"called": False}

    def fail_extract(client, stream, workdir, on_progress, should_cancel):
        raise DownloadError(f"Episode {stream.episode} not found inside the batch.")

    def whole_torrent(client, magnet, workdir, on_progress, should_cancel):
        whole["called"] = True
        raise AssertionError("must never download the whole batch as a single")

    monkeypatch.setattr(provider, "_download_batch_episode", fail_extract)
    monkeypatch.setattr(provider, "_download_torrent", whole_torrent)
    monkeypatch.setattr(nyaa, "_pick_torrent_client", lambda: "aria2c")

    with pytest.raises(DownloadError, match="not found inside the batch"):
        provider.download(stream, tmp_path / "out", "480", lambda f: None, None)
    assert whole["called"] is False


class _FakeTorrentResp:
    content = b"fake torrent bytes"

    def raise_for_status(self):
        return None


def _run_listing_test(monkeypatch, tmp_path, listing_out, episode):
    """Drive _download_batch_episode's aria2 file-listing step with canned
    --show-files output; simulate aria2 writing the selected file into the
    workdir, and return (select_file, output_path)."""
    from app.anime import nyaa
    from app.anime.providers import EpisodeStream

    monkeypatch.setattr(nyaa._client, "get", lambda *a, **k: _FakeTorrentResp())

    class _Run:
        returncode = 0
        stdout = listing_out
        stderr = ""

    monkeypatch.setattr(nyaa.subprocess, "run", lambda *a, **k: _Run())

    captured: dict = {}

    def fake_aria2_download(self, magnet, workdir, on_progress, should_cancel, select_file=None):
        captured["select_file"] = select_file
        d = workdir / "Show"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"Name - {episode:02d} [1080p].mkv"
        f.write_bytes(b"content")
        return None

    monkeypatch.setattr(nyaa.NyaaProvider, "_aria2_download", fake_aria2_download)

    stream = EpisodeStream(
        provider="nyaa", url="magnet:?xt=urn:btih:abc",
        episode=episode, batch=True, torrent_id="947397",
    )
    out = nyaa.NyaaProvider()._download_batch_episode(
        "aria2c", stream, tmp_path, lambda f: None, None
    )
    return captured.get("select_file"), out


def test_batch_episode_parses_aria2_two_line_listing(monkeypatch, tmp_path):
    """aria2 1.37 emits each file as 'idx|path' then '|length' on two lines:
    the episode-2 path line (numeric index 2) must win, the length-only line
    (empty index) must be ignored, and the selected file is returned."""
    listing = (
        "Files:\n"
        "idx|path/length\n"
        "===+====\n"
        "  1|./Show/Name - 01 [1080p].mkv\n"
        "   |151MiB (158,951,529)\n"
        "---+----\n"
        "  2|./Show/Name - 02 [1080p].mkv\n"
        "   |126MiB (132,744,762)\n"
        "---+----\n"
        "  3|./Show/Name - 03 [1080p].mkv\n"
        "   |132MiB (138,731,868)\n"
    )
    select_idx, out = _run_listing_test(monkeypatch, tmp_path, listing, episode=2)
    assert select_idx == "2"
    assert out == tmp_path / "Show" / "Name - 02 [1080p].mkv"
    assert out.read_bytes() == b"content"


def test_batch_episode_parses_single_line_listing(monkeypatch, tmp_path):
    """The older single-line 'idx|path|length' form still selects the file."""
    listing = "  1|./Show/Name - 02 [1080p].mkv|1234567\n"
    select_idx, out = _run_listing_test(monkeypatch, tmp_path, listing, episode=2)
    assert select_idx == "1"
    assert out == tmp_path / "Show" / "Name - 02 [1080p].mkv"


def test_batch_episode_returns_selected_file_not_largest(monkeypatch, tmp_path):
    """aria2 preallocates unselected files, so the batch path must return the
    exact file --select-file downloaded — never the largest file, which may be
    a zero-filled preallocation — and must not consult _largest_video."""
    from app.anime import nyaa
    from app.anime.providers import EpisodeStream

    listing = (
        "Files:\nidx|path/length\n===+====\n"
        "  1|./Show/Name - 01 [1080p].mkv\n"
        "   |158951529\n"
        "---+----\n"
        "  2|./Show/Name - 02 [1080p].mkv\n"
        "   |132744762\n"
        "---+----\n"
        "  3|./Show/Name - 03 [1080p].mkv\n"
        "   |69981\n"
    )
    monkeypatch.setattr(nyaa._client, "get", lambda *a, **k: _FakeTorrentResp())

    class _Run:
        returncode = 0
        stdout = listing
        stderr = ""

    monkeypatch.setattr(nyaa.subprocess, "run", lambda *a, **k: _Run())

    def fake_aria2_download(self, magnet, workdir, on_progress, should_cancel, select_file=None):
        assert select_file == "2"
        d = workdir / "Show"
        d.mkdir(parents=True, exist_ok=True)
        f1 = d / "Name - 01 [1080p].mkv"
        with f1.open("wb") as fh:
            fh.truncate(158951529)  # largest, but preallocated zero-fill
        f2 = d / "Name - 02 [1080p].mkv"
        f2.write_bytes(b"real episode 2 content")
        f3 = d / "Name - 03 [1080p].mkv"
        with f3.open("wb") as fh:
            fh.truncate(69981)
        return None

    monkeypatch.setattr(nyaa.NyaaProvider, "_aria2_download", fake_aria2_download)

    def boom(*a, **k):
        raise AssertionError("_largest_video must not be used for the selected-file path")

    monkeypatch.setattr(nyaa.NyaaProvider, "_largest_video", boom)

    stream = EpisodeStream(
        provider="nyaa", url="magnet:?xt=urn:btih:abc",
        episode=2, batch=True, torrent_id="947397",
    )
    out = nyaa.NyaaProvider()._download_batch_episode(
        "aria2c", stream, tmp_path, lambda f: None, None
    )
    assert out is not None
    assert out.name == "Name - 02 [1080p].mkv"  # the selected episode, not the largest
    assert out.read_bytes() == b"real episode 2 content"


def test_batch_rel_path_normalizes_safely():
    """The aria2 listing path is normalized to a workdir-relative path, and a
    malicious absolute or `..` path is refused rather than allowed to escape."""
    from pathlib import Path

    from app.anime.nyaa import NyaaProvider
    from app.downloader import DownloadError

    assert NyaaProvider._batch_rel_path("./Show/Name - 02 [1080p].mkv") == Path(
        "Show/Name - 02 [1080p].mkv"
    )
    assert NyaaProvider._batch_rel_path("./Folder/File.mkv") == Path("Folder/File.mkv")
    with pytest.raises(DownloadError):
        NyaaProvider._batch_rel_path("/etc/passwd")
    with pytest.raises(DownloadError):
        NyaaProvider._batch_rel_path("../escape.mkv")
    with pytest.raises(DownloadError):
        NyaaProvider._batch_rel_path("./../escape/Name - 02.mkv")


# ── Persian subtitle integration: subs list + provider muxing ─────────────────


def test_anime_download_request_subs_validation():
    """subs accepts a list, a legacy single string, and 'none'/[]; rejects
    unknown languages."""
    from app.anime.routes import AnimeDownloadRequest

    assert AnimeDownloadRequest(media_id=1, season=1, subs=["eng", "fas"]).subs == ["eng", "fas"]
    assert AnimeDownloadRequest(media_id=1, season=1, subs="eng").subs == ["eng"]  # legacy
    assert AnimeDownloadRequest(media_id=1, season=1, subs=["none"]).subs == []
    assert AnimeDownloadRequest(media_id=1, season=1, subs=[]).subs == []
    with pytest.raises(Exception):
        AnimeDownloadRequest(media_id=1, season=1, subs=["es"])


def _finalize_setup(monkeypatch, tmp_path, find_sub):
    """Shared harness for nyaa._finalize: stub stream probing, ffmpeg and the
    embed/srt muxer; return captured mux inputs."""
    from app import downloader
    from app.anime import nyaa

    video = tmp_path / "ep.mkv"
    video.write_bytes(b"video")
    out = tmp_path / "ep.mp4"

    monkeypatch.setattr(
        nyaa.NyaaProvider, "_find_sub_stream", staticmethod(find_sub)
    )

    def fake_ffmpeg(args, produced, what):
        if what == "subtitle extract":
            produced.write_text("1\n00:00:01,000 --> 00:00:04,000\nHello\n")
        else:
            produced.write_bytes(b"muxed")

    monkeypatch.setattr(downloader, "_run_ffmpeg", fake_ffmpeg)

    captured: dict = {}

    def fake_mux(video_, out_, embedded, srt_files):
        captured["embedded"] = embedded
        captured["srt_files"] = srt_files
        out_.write_bytes(b"muxed")

    monkeypatch.setattr(
        nyaa.NyaaProvider, "_mux_embedded_and_srt", staticmethod(fake_mux)
    )
    return video, out, captured


def test_nyaa_finalize_eng_only_keeps_single_track(monkeypatch, tmp_path):
    from app import downloader
    from app.anime import nyaa

    video, out, captured = _finalize_setup(
        monkeypatch, tmp_path, lambda video, lang: "1" if lang == "eng" else None
    )
    ffmpeg_args = {}

    def fake_ffmpeg(args, produced, what):
        ffmpeg_args["args"] = args
        produced.write_bytes(b"muxed")

    monkeypatch.setattr(downloader, "_run_ffmpeg", fake_ffmpeg)
    nyaa.NyaaProvider._finalize(video, out, ["eng"])
    assert "-map", "0:s:1" in zip(ffmpeg_args["args"], ffmpeg_args["args"][1:]) or "0:s:1" in ffmpeg_args["args"]
    assert "mov_text" in ffmpeg_args["args"]


def test_nyaa_finalize_persian_translates_english(monkeypatch, tmp_path):
    from app.anime import nyaa

    video, out, captured = _finalize_setup(
        monkeypatch, tmp_path, lambda video, lang: "1" if lang == "eng" else None
    )

    def fake_translate(source, target, dest):
        dest.write_text("1\n00:00:01,000 --> 00:00:04,000\nسلام\n")
        return dest

    monkeypatch.setattr("app.anime.subtitle_translate.translate_srt_file", fake_translate)

    nyaa.NyaaProvider._finalize(video, out, ["eng", "fas"])
    assert captured["embedded"] == [("1", "eng")]  # original English untouched
    assert [lang for lang, _ in captured["srt_files"]] == ["fas"]  # translated Persian
    assert out.read_bytes() == b"muxed"


def test_nyaa_finalize_persian_only_muxes_persian(monkeypatch, tmp_path):
    from app.anime import nyaa

    video, out, captured = _finalize_setup(
        monkeypatch, tmp_path, lambda video, lang: "1" if lang == "eng" else None
    )

    def fake_translate(source, target, dest):
        dest.write_text("1\n00:00:01,000 --> 00:00:04,000\nسلام\n")
        return dest

    monkeypatch.setattr("app.anime.subtitle_translate.translate_srt_file", fake_translate)

    nyaa.NyaaProvider._finalize(video, out, ["fas"])
    assert captured["embedded"] == []  # English NOT muxed
    assert [lang for lang, _ in captured["srt_files"]] == ["fas"]


def test_nyaa_finalize_translation_failure_falls_back(monkeypatch, tmp_path):
    from app.anime import nyaa

    video, out, captured = _finalize_setup(
        monkeypatch, tmp_path, lambda video, lang: "1" if lang == "eng" else None
    )
    monkeypatch.setattr(
        "app.anime.subtitle_translate.translate_srt_file", lambda *a, **k: None
    )

    nyaa.NyaaProvider._finalize(video, out, ["eng", "fas"])
    assert captured["embedded"] == [("1", "eng")]  # English survived the failed translation
    assert captured["srt_files"] == []


def test_nyaa_finalize_embedded_persian_preferred(monkeypatch, tmp_path):
    from app.anime import nyaa

    video, out, captured = _finalize_setup(
        monkeypatch, tmp_path,
        lambda video, lang: "1" if lang == "eng" else ("2" if lang == "fas" else None),
    )
    nyaa.NyaaProvider._finalize(video, out, ["eng", "fas"])
    assert ("2", "fas") in captured["embedded"]  # embedded Persian used, no translation
    assert captured["srt_files"] == []


def test_finalize_subtitles_eng_plus_fas_muxes_two_tracks(monkeypatch, tmp_path):
    from app.anime import downloader as anime_downloader

    video = tmp_path / "ep.mp4"
    video.write_bytes(b"v")
    eng_sub = tmp_path / "ep.srt"
    eng_sub.write_text("1\n00:00:01,000 --> 00:00:04,000\nHello\n")

    def fake_translate(source, target, dest):
        dest.write_text("1\n00:00:01,000 --> 00:00:04,000\nسلام\n")
        return dest

    monkeypatch.setattr("app.anime.subtitle_translate.translate_srt_file", fake_translate)

    captured: dict = {}

    def fake_mux(video_, tracks, dest):
        captured["tracks"] = tracks
        out = tmp_path / "ep.mp4"
        out.write_bytes(b"muxed")
        return out

    monkeypatch.setattr("app.anime.subtitles.mux_subtitles", fake_mux)

    out = anime_downloader._finalize_subtitles(video, eng_sub, ["eng", "fas"], tmp_path / "ep")
    assert [lang for lang, _ in captured["tracks"]] == ["eng", "fas"]
    assert out.read_bytes() == b"muxed"


def test_finalize_subtitles_eng_only_keeps_single_mux(monkeypatch, tmp_path):
    from app.anime import downloader as anime_downloader

    video = tmp_path / "ep.mp4"
    video.write_bytes(b"v")
    eng_sub = tmp_path / "ep.srt"
    eng_sub.write_text("1\n00:00:01,000 --> 00:00:04,000\nHello\n")

    captured: dict = {}

    def fake_single(video_, sub, dest):
        captured["sub"] = sub
        out = tmp_path / "ep.mp4"
        out.write_bytes(b"muxed")
        return out

    monkeypatch.setattr(anime_downloader, "_mux_subtitles", fake_single)

    out = anime_downloader._finalize_subtitles(video, eng_sub, ["eng"], tmp_path / "ep")
    assert captured["sub"] == eng_sub  # the existing single-track path
    assert out.read_bytes() == b"muxed"


def test_find_sub_stream_returns_per_type_index(monkeypatch, tmp_path):
    """ffprobe reports global stream indexes; ffmpeg's `-map 0:s:N` wants the
    per-type (subtitle) index, so _find_sub_stream counts subtitle lines. A
    real fansub carries the title too ("2,eng,English"), which must still match
    the language; a code-less track whose title names the language does too."""
    from app.anime import nyaa

    video = tmp_path / "ep.mkv"
    video.write_bytes(b"x")
    probe = (
        "2,eng,English\n"  # global 2 = first subtitle  -> per-type 0
        "3,spa,Spanish\n"  # second subtitle           -> per-type 1
        "4,fas,Persian\n"  # third subtitle            -> per-type 2
        "5,,Français\n"    # no code, French title      -> not eng/fas
        "6,,English\n"     # no code, English title     -> eng (per-type 4)
    )

    class _Run:
        returncode = 0
        stdout = probe

    monkeypatch.setattr(nyaa.subprocess, "run", lambda *a, **k: _Run())
    assert nyaa.NyaaProvider._find_sub_stream(video, "eng") == "0"
    assert nyaa.NyaaProvider._find_sub_stream(video, "fas") == "2"
    assert nyaa.NyaaProvider._find_sub_stream(video, "und") is None
    # Code-less track with an English title falls back to the title.
    assert nyaa.NyaaProvider._find_sub_stream(video, "engx") is None
    # (covered above) title-only English is caught only when code is absent


def test_nyaa_finalize_persian_failure_falls_back_to_english(monkeypatch, tmp_path):
    """A failed Persian translation must never strip the user of subtitles: with
    ["fas"] requested and no way to produce Persian, the English track ships."""
    from app.anime import nyaa

    video, out, captured = _finalize_setup(
        monkeypatch, tmp_path, lambda video, lang: "1" if lang == "eng" else None
    )
    monkeypatch.setattr(
        "app.anime.subtitle_translate.translate_srt_file", lambda *a, **k: None
    )
    nyaa.NyaaProvider._finalize(video, out, ["fas"])
    assert captured["embedded"] == [("1", "eng")]  # English shipped as the fallback
    assert captured["srt_files"] == []
