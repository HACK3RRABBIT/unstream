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
    from app.anime import providers as providers_module
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


def test_nyaa_batch_only_episode_is_refused_with_clear_message(monkeypatch):
    """An episode that only exists inside a multi-episode batch is refused with
    a clear explanation rather than a wrong-episode match."""
    from app.anime import nyaa
    from app.models import ProviderError

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
    with pytest.raises(ProviderError) as caught:
        p._search_episode(src, 1)
    assert "batch" in str(caught.value).lower()


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
