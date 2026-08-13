"""Nyaa.si torrent provider — the complete, free, permanent anime archive.

Nyaa is the community's archive backbone: every anime, every episode, every
quality, sub/dub/raw, free and keyless since 2007. Its main category is
literally "Anime - English-translated", so English-subbed fansubs land within
hours of an episode airing. It is the one source this project can rely on for
years, and it does not IP-block (verified reachable from this box).

A torrent is not a single stream, so this provider self-downloads: it finds
the best-seeded torrent matching (title, season, episode) on Nyaa, fetches it
with a torrent client (libtorrent when available — the Docker image runs
Python 3.12 which has wheels; aria2c as a fallback), and returns the video
file for the muxer to finalize.

Torrents mean the first download of an episode waits for seeders before bytes
flow — the trade for completeness and permanence.
"""

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import httpx

from ..downloader import Cancelled, DownloadError
from ..models import ProviderError
from .providers import EpisodeSource, EpisodeStream

BASE_URL = "https://nyaa.si"
# The English-translated category filter (f=0 means "no filter", c=1_2 means
# "Anime - English-translated"). We narrow to English subs per the project's
# focus; other sub languages exist under other category ids.
_CATEGORY_ENGLISH = "1_2"

_TIMEOUT = 25
_client = httpx.Client(
    headers={
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    },
    timeout=_TIMEOUT,
    follow_redirects=True,
)

# How long to wait for a torrent to make progress before giving up (seconds).
# The first minutes are tracker announce + piece discovery; a fresh swarm can
# take ~30s to ramp. Past this with nothing, the swarm is likely dead.
_TORRENT_STALL_SECONDS = 240

# Public UDP trackers as a fallback — some Nyaa swarms only announce here.
_PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://tracker.leechers-paradise.org:6969/announce",
    "udp://open.stealth.si:80/announce",
    "udp://exodus.desync.com:6969/announce",
]


def _magnet(btih: str, name: str) -> str:
    return f"magnet:?xt=urn:btih:{btih}&dn={quote(name)}"


class NyaaProvider:
    name = "nyaa"
    # The provider fetches the torrent itself (torrents aren't an HLS url), so
    # the downloader must call download() rather than yt-dlp on a url.
    streams_hls = False

    def available(self) -> bool:
        return True  # keyless and free; no credentials

    def resolve(self, title: str, year: int | None) -> EpisodeSource:
        """Find the anime on Nyaa; the search term is the whole plan."""
        # Nyaa keys everything by search text, so the "id" is the title the
        # per-episode search will use. Season/year are carried through so the
        # per-episode search can disambiguate remakes.
        return EpisodeSource(
            provider=self.name,
            anime_id=title,
            anime_title=title,
            year=year,
            season=0,
            episode=0,
        )

    def _search_episode(self, src: EpisodeSource, episode: int) -> dict:
        """Return the best-seeded torrent dict for one episode.

        The year is deliberately left out of the search query — Nyaa's search
        treats parentheses specially and "(1999)" zeroes out results. The
        episode number in the title is the strong disambiguator.
        """
        title = src.anime_id
        query = f"{title} {episode}".strip()
        try:
            resp = _client.get(
                f"{BASE_URL}/",
                params={
                    "f": 0,
                    "c": _CATEGORY_ENGLISH,
                    "q": query,
                    "s": "seeders",
                    "o": "desc",
                },
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Could not reach Nyaa: {exc}") from exc

        # Rows: category, title, download, size, seeders, leechers, date.
        # The real torrent title is on the /view/<id> link's title attribute
        # (the earlier title= is the category name). We require the row's
        # title to mention the episode number, so a "One Piece 1100" query
        # doesn't match a full-season batch.
        rows = re.findall(r"<tr[^>]*>.*?</tr>", resp.text, re.S)
        best = None
        for row in rows[1:]:  # first row is the header
            title_m = re.search(r'href="/view/\d+"[^>]*title="([^"]+)"', row)
            magnet = re.search(r'href="(magnet:\?[^"]+)"', row)
            seeders_m = re.search(r'class="text-center"[^>]*>(\d+)<', row)
            size_m = re.search(r"([\d.]+\s+(?:GiB|MiB))", row)
            if not title_m or not magnet:
                continue
            title = title_m.group(1)
            # The episode must appear as a standalone number in the title.
            if not re.search(rf"(?<!\d){episode}(?!\d)", title):
                continue
            seeders = int(seeders_m.group(1)) if seeders_m else 0

            # Score how precisely this title names our episode, so a single
            # "EP 01" release beats a "S00E01-E06" batch that merely contains
            # episode 1. Explicit episode markers are weighted over a bare
            # number; batch ranges (a dash between two episode numbers, e.g.
            # "E01-E06" or "01-06") are penalized.
            marker = r"(?:EP|Ep|Episode|E|#)\s*0*%d(?!\d)" % episode
            explicit = bool(re.search(marker, title))
            batch = bool(
                re.search(rf"(?:EP|Ep|Episode|E)?\s*0*{episode}\s*[-–—]\s*0*\d+", title)
            )
            score = seeders + (200 if explicit else 0) - (100 if batch else 0)
            if explicit and batch:
                score = seeders - 100  # a range that names it, not a single
            if best is None or score > best["score"]:
                best = {
                    "title": title,
                    "magnet": magnet.group(1),
                    "seeders": seeders,
                    "size": size_m.group(1) if size_m else "",
                    "score": score,
                }
        if best is None or best["seeders"] == 0:
            raise ProviderError(
                f"No seeded torrent found on Nyaa for '{query}'."
            )
        return best

    def episode_count(self, src: EpisodeSource) -> int | None:
        """Nyaa has no per-show episode registry — the episode number is in
        the torrent title. Unknown until searched, so None (the route then
        needs an explicit episode selection rather than a whole-season batch,
        which Nyaa cannot enumerate).
        """
        return None

    def episode_stream(self, src: EpisodeSource, quality: str) -> EpisodeStream:
        """The best-seeded magnet for `src.episode`."""
        torrent = self._search_episode(src, src.episode)
        return EpisodeStream(
            provider=self.name,
            url=torrent["magnet"],
            headers={},
        )

    def download(
        self,
        stream: EpisodeStream,
        dest: Path,
        quality: str,
        on_progress: Callable[[float], None],
        should_cancel: Callable[[], bool] | None,
    ) -> Path:
        """Download the torrent, extract the video file, return it as mp4.

        `dest` is a stem (no extension). The torrent may contain one video
        (mkv/mp4) or a folder; we find the largest video and rename/remux it
        to dest.mp4 so the job's file naming holds.
        """
        magnet = stream.url
        if not magnet or not magnet.startswith("magnet:"):
            raise DownloadError("Nyaa stream has no magnet link.")

        workdir = dest.parent / f"{dest.name}.nyaatmp"
        workdir.mkdir(parents=True, exist_ok=True)

        client = _pick_torrent_client()
        video = self._download_torrent(client, magnet, workdir, on_progress, should_cancel)
        if video is None:
            raise DownloadError("No video file found in the torrent.")

        out = dest.with_name(dest.name + ".mp4")
        if video.suffix.lower() == ".mp4":
            video.rename(out)
        else:
            from ..downloader import _run_ffmpeg

            _run_ffmpeg(["-i", str(video), "-c", "copy"], out, "torrent mux")
            video.unlink(missing_ok=True)
        # Clean the torrent working directory.
        shutil.rmtree(workdir, ignore_errors=True)
        return out

    def _download_torrent(self, client: str, magnet: str, workdir: Path,
                          on_progress: Callable[[float], None],
                          should_cancel: Callable[[], bool] | None) -> Path | None:
        """Run the torrent client and return the largest video file produced."""
        if client == "aria2c":
            return self._aria2_download(magnet, workdir, on_progress, should_cancel)
        return self._libtorrent_download(magnet, workdir, on_progress, should_cancel)

    def _aria2_download(self, magnet: str, workdir: Path,
                        on_progress: Callable[[float], None],
                        should_cancel: Callable[[], bool] | None) -> Path | None:
        # DHT + public trackers find peers even when the magnet's own trackers
        # are unreachable — the reason a fresh torrent often stalls at 0%.
        cmd = [
            "aria2c",
            "--dir", str(workdir),
            "--seed-time=0",
            "--enable-dht=true",
            "--bt-tracker-timeout=10",
            "--bt-stop-timeout=300",
            "--summary-interval=5",
            "--console-log-level=warn",
            "--bt-tracker=" + ",".join(_PUBLIC_TRACKERS),
            magnet,
        ]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError as exc:
            raise DownloadError(f"Could not run aria2c: {exc}") from exc
        # Poll for progress / completion / cancellation.
        started = time.monotonic()
        while proc.poll() is None:
            if should_cancel and should_cancel():
                proc.terminate()
                raise Cancelled()
            if time.monotonic() - started > _TORRENT_STALL_SECONDS:
                proc.terminate()
                raise DownloadError("Torrent made no progress in time.")
            time.sleep(2)
        if proc.returncode != 0:
            raise DownloadError("aria2c failed to download the torrent.")
        return self._largest_video(workdir)

    def _libtorrent_download(self, magnet: str, workdir: Path,
                             on_progress: Callable[[float], None],
                             should_cancel: Callable[[], bool] | None) -> Path | None:
        import libtorrent as lt

        session = lt.session({"listen_interfaces": "0.0.0.0:6881"})
        params = lt.parse_magnet_uri(magnet)
        params.save_path = str(workdir)
        # The magnet's own trackers plus public fallbacks — some Nyaa swarms
        # only announce on the public UDP trackers.
        params.trackers = [
            "udp://tracker.opentrackr.org:1337/announce",
            "udp://tracker.openbittorrent.com:6969/announce",
            "udp://tracker.leechers-paradise.org:6969/announce",
        ]
        handle = session.add_torrent(params)
        started = time.monotonic()
        last_progress = -1.0
        while not handle.is_seed():
            if should_cancel and should_cancel():
                session.pause()
                raise Cancelled()
            # Trackers announce over a few seconds; give a fresh torrent time
            # to find peers before declaring it stalled.
            if time.monotonic() - started > _TORRENT_STALL_SECONDS:
                session.pause()
                raise DownloadError("Torrent made no progress in time.")
            status = handle.status()
            if status.progress > last_progress + 0.02:
                on_progress(status.progress)
                last_progress = status.progress
            time.sleep(1)
        session.pause()
        return self._largest_video(workdir)

    @staticmethod
    def _largest_video(workdir: Path) -> Path | None:
        vids = [
            p
            for p in workdir.rglob("*")
            if p.is_file() and p.suffix.lower() in (".mp4", ".mkv", ".avi", ".webm")
        ]
        return max(vids, key=lambda p: p.stat().st_size) if vids else None


def _pick_torrent_client() -> str:
    """Prefer aria2c (simplest, no Python-version wheel issues); else libtorrent."""
    if shutil.which("aria2c"):
        return "aria2c"
    try:
        import libtorrent  # noqa: F401

        return "libtorrent"
    except ImportError:
        raise DownloadError(
            "No torrent client available — install aria2c or the libtorrent Python package."
        )
