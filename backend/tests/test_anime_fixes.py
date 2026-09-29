"""Regression tests for the anime pipeline's plumbing — no network.

Each test pins a bug that shipped: fallback providers asked for episode 0,
"Fate/Zero" broke its own plan URL, a cancelled torrent was retried on the
next provider, and the anime clients ignored the desktop's proxy setting.
"""

import httpcore
import pytest

from app import net
from app.anime import anilist, nyaa
from app.anime import downloader as anime_downloader
from app.anime import routes as anime_routes
from app.anime.providers import EpisodeSource, EpisodeStream
from app.downloader import Cancelled


def _media(**overrides) -> anilist.AniMedia:
    fields = dict(
        id=33, title_romaji="Fate/Zero", title_english="Fate/Zero #1", format="TV",
        episodes=13, season_year=2011, status="FINISHED",
    )
    fields.update(overrides)
    return anilist.AniMedia(**fields)


def test_plan_url_survives_slashes_and_hashes_in_titles():
    season = _media(title_romaji="Sousou no Frieren", title_english="Frieren/#Beyond")
    plan = EpisodeSource(
        provider="nyaa", anime_id="Frieren/#Beyond", anime_title="Frieren/#Beyond",
        year=2023, season=0, episode=0,
    )
    url = anime_routes._plan_url(plan, season, 1, 7)
    src = anime_downloader.parse_source_url(url)
    assert (src.provider, src.anime_id, src.season, src.episode) == ("nyaa", "Frieren/#Beyond", 1, 7)
    assert src.anime_title == "Frieren/#Beyond"
    assert src.anilist_id == 33
    assert src.alt_titles == ("Sousou no Frieren",)


def test_legacy_plan_urls_still_parse():
    src = anime_downloader.parse_source_url("anime://nyaa/One%20Piece/1/1100#anilist=21&title=ONE%20PIECE")
    assert (src.anime_id, src.episode, src.anime_title, src.alt_titles) == (
        "One Piece", 1100, "ONE PIECE", ()
    )


def test_malformed_plan_is_a_download_error():
    with pytest.raises(anime_downloader.DownloadError):
        anime_downloader.parse_source_url("anime://nyaa/Fate/Zero/1/2")


class _FallbackProvider:
    name = "nyaa"

    def resolve(self, title, year, anilist_id=None):
        # Every real provider answers with the show, not the episode.
        return EpisodeSource(self.name, title, title, year, 0, 0)


def test_reanchor_keeps_the_plans_episode_season_and_ids():
    plan = EpisodeSource(
        provider="anivexa", anime_id="21", anime_title="ONE PIECE", year=None,
        season=1, episode=1100, anilist_id=21, alt_titles=("One Piece",),
    )
    src = anime_downloader._reanchor(_FallbackProvider(), plan)
    assert src.provider == "nyaa"
    assert src.anime_id == "ONE PIECE"
    assert (src.season, src.episode, src.anilist_id, src.alt_titles) == (1, 1100, 21, ("One Piece",))


def test_nyaa_download_lets_a_cancel_through(monkeypatch, tmp_path):
    monkeypatch.setattr(nyaa, "_pick_torrent_client", lambda: "aria2c")

    def cancelled(*a, **k):
        raise Cancelled()

    monkeypatch.setattr(nyaa.NyaaProvider, "_download_torrent", cancelled)
    stream = EpisodeStream(provider="nyaa", url="magnet:?xt=urn:btih:abc", episode=1)
    with pytest.raises(Cancelled):
        nyaa.NyaaProvider().download(stream, tmp_path / "ep", "original", lambda f: None, None)
    assert not (tmp_path / "ep.nyaatmp").exists()


def test_nyaa_download_error_carries_no_traceback(monkeypatch, tmp_path):
    monkeypatch.setattr(nyaa, "_pick_torrent_client", lambda: "aria2c")

    def broken(*a, **k):
        raise OSError("disk went away")

    monkeypatch.setattr(nyaa.NyaaProvider, "_download_torrent", broken)
    stream = EpisodeStream(provider="nyaa", url="magnet:?xt=urn:btih:abc", episode=1)
    with pytest.raises(nyaa.DownloadError) as info:
        nyaa.NyaaProvider().download(stream, tmp_path / "ep", "original", lambda f: None, None)
    assert "Traceback" not in str(info.value)
    assert "disk went away" in str(info.value)


def test_ffmpeg_banner_gives_dimensions_without_ffprobe():
    banner = (
        "Input #0, matroska,webm, from 'ep.mkv':\n"
        "  Stream #0:0(jpn): Video: hevc (Main 10), yuv420p10le(tv), 1920x804 [SAR 1:1 DAR 160:67], 23.98 fps\n"
        "  Stream #0:1(jpn): Audio: aac, 48000 Hz, stereo\n"
    )
    match = anime_downloader._FFMPEG_VIDEO_RE.search(banner)
    assert (int(match.group(1)), int(match.group(2))) == (1920, 804)


def test_episode_duration_is_minutes_not_hours():
    # Checked through the route's constant rather than a request: 24 minutes.
    import inspect

    assert "24 * 60 * 1000" in inspect.getsource(anime_routes.anime_download)


@pytest.fixture
def restore_proxy():
    before = net.setting()
    yield
    net.configure(before)


def _pool_for(client, url):
    return client._transport._transport_for(url)._pool


def test_anime_http_client_follows_the_proxy_setting(restore_proxy):
    client = net.http_client(timeout=1)
    net.configure("http://127.0.0.1:10809")
    assert isinstance(_pool_for(client, "https://nyaa.si/"), httpcore.HTTPProxy)
    net.configure("socks5://127.0.0.1:10808")
    assert isinstance(_pool_for(client, "https://nyaa.si/"), httpcore.SOCKSProxy)
    net.configure("off")
    assert isinstance(_pool_for(client, "https://nyaa.si/"), httpcore.ConnectionPool)


def test_nyaa_client_is_routed():
    assert isinstance(nyaa._client._transport, net._HttpxRouter)


def test_libtorrent_proxy_only_for_an_explicit_proxy(restore_proxy):
    net.configure("system")
    assert nyaa._lt_proxy_settings() == {"proxy_type": 0}
    net.configure("socks5://user:pw@127.0.0.1:10808")
    settings = nyaa._lt_proxy_settings()
    assert (settings["proxy_type"], settings["proxy_hostname"], settings["proxy_port"]) == (3, "127.0.0.1", 10808)
    assert settings["proxy_username"] == "user" and settings["proxy_peer_connections"]


def test_hianime_episode_lookup_reads_both_attribute_orders():
    from app.anime import hianime

    html = (
        '<a data-number="1" class="x" data-id="501">1</a>'
        '<a data-id="502" class="x" data-number="2">2</a>'
    )
    assert hianime._pick_episode_id(html, 1) == "501"
    assert hianime._pick_episode_id(html, 2) == "502"
    assert hianime._show_id("one-piece-100") == "100"


def test_vtt_timestamps_become_valid_srt():
    from app.anime import subtitles

    srt = subtitles.normalize_srt(b"WEBVTT\n\n00:00.500 --> 01:02:03.250 align:start\nHi\n")
    assert "00:00:00,500 --> 01:02:03,250" in srt


def test_a_subtitle_that_wont_mux_keeps_the_episode(monkeypatch, tmp_path):
    video = tmp_path / "ep.mp4"
    video.write_bytes(b"video")
    sub = tmp_path / "ep.srt"
    sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n")

    def broken(*a, **k):
        raise anime_downloader.DownloadError("ffmpeg subtitle mux failed")

    monkeypatch.setattr(anime_downloader, "_mux_subtitles", broken)
    assert anime_downloader._finalize_subtitles(video, sub, ["eng"], tmp_path / "ep") == video
    assert video.read_bytes() == b"video"


@pytest.mark.skipif(__import__("shutil").which("ffmpeg") is None, reason="needs ffmpeg")
def test_subtitle_mux_really_produces_an_mp4_track(tmp_path):
    import subprocess

    video = tmp_path / "ep.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=5",
         "-t", "1", "-c:v", "libx264", str(video)],
        check=True,
    )
    sub = tmp_path / "ep.srt"
    sub.write_bytes(b"WEBVTT\n\n00:00.100 --> 00:00.900\nHello\n")
    out = anime_downloader._finalize_subtitles(video, sub, ["eng"], tmp_path / "ep")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:stream_tags=language",
         "-of", "csv=p=0", str(out)],
        capture_output=True, text=True,
    ).stdout.split()
    assert "subtitle,eng" in probe
    assert not list(tmp_path.glob("*.subbed*"))
