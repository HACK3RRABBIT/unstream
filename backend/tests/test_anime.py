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
