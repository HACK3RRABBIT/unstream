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

    def _search_episode(self, src: EpisodeSource, episode: int, quality: str = "") -> dict:
        """Return the best-seeded torrent dict for one episode.

        Tries the `S{season}E{episode}` form first when the season is known
        (so "JUJUTSU KAISEN S01E01" isn't matched as S03E01), then falls back
        to the bare episode number for series that Nyaa names by number alone
        (One Piece "1100"). The year is left out (parentheses break Nyaa's
        search), and quality is not filtered (torrents carry whatever
        resolution the fansub released).
        """
        title = src.anime_id
        queries = []
        if src.season > 0:
            queries.append(f"{title} S{src.season:02d}E{episode:02d}")
        queries.append(f"{title} {episode}")

        best: dict | None = None
        best_batch: dict | None = None
        for query in queries:
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

            single, batch = self._parse_rows(resp.text, episode)
            if single and (best is None or single["seeders"] > best["seeders"]):
                best = single
            if batch and (best_batch is None or batch["seeders"] > best_batch["seeders"]):
                best_batch = batch
            if best:
                break  # a true single-episode match; don't fall back

        if (best is None or best["seeders"] == 0) and best_batch is not None:
            best = {**best_batch, "batch": True}
        if best is None or best["seeders"] == 0:
            raise ProviderError(
                f"No seeded torrent containing '{title}' episode {episode} found on Nyaa."
            )
        return best

    @staticmethod
    def _parse_rows(html: str, episode: int) -> tuple[dict | None, dict | None]:
        """Parse a Nyaa search page into (best single-episode, best batch).

        An episode number is "real" when it appears with a delimiter or marker
        (EP 01, E01, - 001, (001), 001 [) — never as bare digits that could be
        a hash, a year, or a resolution. A range (001-574, E01-E06) is a
        batch: the episode is in it, and it's kept as an extractable fallback.
        """
        # An episode number is "real" when it appears with a delimiter or
        # marker (EP 01, E01, - 001, (001), 001 [) — never as bare digits
        # that could be a hash, a year, or a resolution.
        ep_re = re.compile(
            rf"(?:EP|Ep|Episode|E|#)\s*0*{episode}(?!\d)"
            rf"|(?:^|[\s\-–—(\[])\s*0*{episode}\s*(?=[\]\s\-–—,]|$)"
        )
        # A range of episodes (001-574, E01-E06) — the episode is *in* it but
        # the torrent is a whole batch; we can still extract the single file.
        batch_re = re.compile(rf"(?:EP|Ep|Episode|E)?\s*0*{episode}\s*[-–—]\s*0*\d+")

        rows = re.findall(r"<tr[^>]*>.*?</tr>", html, re.S)
        best: dict | None = None
        best_batch: dict | None = None
        for row in rows[1:]:  # first row is the header
            title_m = re.search(r'href="/view/\d+"[^>]*title="([^"]+)"', row)
            magnet = re.search(r'href="(magnet:\?[^"]+)"', row)
            view_m = re.search(r'href="(/view/\d+)"', row)
            seeders_m = re.search(r'class="text-center"[^>]*>(\d+)<', row)
            size_m = re.search(r"([\d.]+\s+(?:GiB|MiB))", row)
            if not title_m or not magnet:
                continue
            title = title_m.group(1)
            if not ep_re.search(title):
                continue
            seeders = int(seeders_m.group(1)) if seeders_m else 0
            common = {
                "title": title,
                "magnet": magnet.group(1),
                "torrent_id": (view_m.group(1) if view_m else "").rsplit("/", 1)[-1],
                "seeders": seeders,
                "size": size_m.group(1) if size_m else "",
            }

            marker = r"(?:EP|Ep|Episode|E|#)\s*0*%d(?!\d)" % episode
            explicit = bool(re.search(marker, title))
            is_batch = bool(batch_re.search(title))
            if is_batch:
                # Keep the best-seeded batch as a fallback for extraction.
                if best_batch is None or seeders > best_batch["seeders"]:
                    best_batch = common
                continue
            score = seeders + (200 if explicit else 0)
            if best is None or score > best["score"]:
                best = {**common, "score": score}
        return best, best_batch

    def episode_count(self, src: EpisodeSource) -> int | None:
        """Nyaa has no per-show episode registry — the episode number is in
        the torrent title. Unknown until searched, so None (the route then
        needs an explicit episode selection rather than a whole-season batch,
        which Nyaa cannot enumerate).
        """
        return None

    def episode_stream(self, src: EpisodeSource, quality: str) -> EpisodeStream:
        """The best-seeded magnet for `src.episode`, at `quality` when offered.

        When the episode only exists inside a batch, the stream carries the
        batch flag + torrent id so download() can extract just that episode.
        """
        torrent = self._search_episode(src, src.episode, quality)
        return EpisodeStream(
            provider=self.name,
            url=torrent["magnet"],
            headers={},
            episode=src.episode,
            batch=torrent.get("batch", False),
            torrent_id=torrent.get("torrent_id", ""),
        )

    def download(
        self,
        stream: EpisodeStream,
        dest: Path,
        quality: str,
        on_progress: Callable[[float], None],
        should_cancel: Callable[[], bool] | None,
        subs: str = "eng",
    ) -> Path:
        """Download the torrent, extract the video, return it as mp4.

        `dest` is a stem (no extension). Anime fansubs embed soft subtitle
        tracks inside the mkv; when the user asked for subtitles, the track
        matching the requested language is muxed into the mp4 (mov_text) so it
        can be toggled in any player. `subs="none"` keeps the video bare.
        """
        magnet = stream.url
        if not magnet or not magnet.startswith("magnet:"):
            raise DownloadError("Nyaa stream has no magnet link.")

        workdir = dest.parent / f"{dest.name}.nyaatmp"
        workdir.mkdir(parents=True, exist_ok=True)

        client = _pick_torrent_client()
        # A batch torrent needs the single episode's file selected up front;
        # a single-episode torrent downloads whole.
        if stream.batch and stream.torrent_id:
            video = self._download_batch_episode(
                client, stream, workdir, on_progress, should_cancel
            )
        else:
            video = self._download_torrent(client, magnet, workdir, on_progress, should_cancel)
        if video is None:
            raise DownloadError("No video file found in the torrent.")

        out = dest.with_name(dest.name + ".mp4")
        if video.suffix.lower() == ".mp4":
            video.rename(out)
        else:
            self._finalize(video, out, subs)
        # Clean the torrent working directory.
        shutil.rmtree(workdir, ignore_errors=True)
        return out

    def _download_batch_episode(self, client: str, stream: EpisodeStream, workdir: Path,
                                on_progress: Callable[[float], None],
                                should_cancel: Callable[[], bool] | None) -> Path | None:
        """Download only the requested episode's file from a batch torrent.

        Fetch the .torrent (Nyaa serves /download/<id>.torrent), list its
        files with aria2 --show-files, find the file whose name carries the
        episode number, and select just that file index so a whole season
        batch doesn't download to extract one episode.
        """
        torrent_url = f"{BASE_URL}/download/{stream.torrent_id}.torrent"
        try:
            resp = _client.get(torrent_url, timeout=_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise DownloadError(f"Could not fetch torrent file: {exc}") from exc

        torrent_file = workdir / f"batch-{stream.torrent_id}.torrent"
        torrent_file.write_bytes(resp.content)

        # aria2 lists files as "idx|path|length" lines.
        listing = subprocess.run(
            ["aria2c", "--show-files", str(torrent_file)],
            capture_output=True, text=True, timeout=30,
        )
        if listing.returncode != 0:
            raise DownloadError("Could not list torrent files.")
        if client == "aria2c":
            target_idx = None
            for line in listing.stdout.splitlines():
                # "1|/folder/Name - 01 [1080p].mkv|1234567"
                parts = line.split("|")
                if len(parts) >= 3 and self._file_is_episode(parts[1], stream.episode):
                    target_idx = parts[0]
                    break
            if target_idx is None:
                raise DownloadError(
                    f"Episode {stream.episode} not found inside the batch."
                )
            return self._aria2_download(
                str(torrent_file), workdir, on_progress, should_cancel,
                select_file=target_idx,
            )
        # libtorrent: find the file by name and set priorities.
        return self._libtorrent_batch_download(
            str(torrent_file), workdir, stream.episode, on_progress, should_cancel
        )

    @staticmethod
    def _file_is_episode(path: str, episode: int) -> bool:
        """Does a batch file name identify this episode (EP 01 / E01 / - 01)?"""
        return bool(
            re.search(
                rf"(?:EP|Ep|Episode|E|#)\s*0*{episode}(?!\d)"
                rf"|(?:^|[\s\-–—(\[])\s*0*{episode}\s*(?=[\]\s\-–—.]|$)",
                path,
            )
        )

    @staticmethod
    def _finalize(video: Path, out: Path, subs: str) -> None:
        """Remux a non-mp4 video to mp4, muxing the requested subtitle track.

        `subs` is "eng"/"fas"/"none". The subtitle track is matched from the
        file's embedded stream metadata (language/title); when none matches the
        requested language, the video is muxed without subtitles rather than
        failing the download — subtitles are nice-to-have.
        """
        from ..downloader import _run_ffmpeg

        if subs == "none":
            _run_ffmpeg(["-i", str(video), "-c", "copy"], out, "torrent mux")
            video.unlink(missing_ok=True)
            return

        # Probe the video's streams: index + language per stream.
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "s",
                "-show_entries", "stream=index:stream_tags=language,title",
                "-of", "csv=p=0", str(video),
            ],
            capture_output=True, text=True, timeout=30,
        )
        target = "eng" if subs == "eng" else "fas"
        chosen = None
        for line in probe.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            idx = parts[0]
            tags = " ".join(parts[1:]).lower()
            # Match English (eng/en) or Persian (fas/fa/per) language tags.
            if subs == "eng" and re.search(r"(^|_)eng?($|_)", tags.replace("-", "_")):
                chosen = idx
                break
            if subs == "fas" and re.search(r"(^|_)fas|per|fa($|_)", tags.replace("-", "_")):
                chosen = idx
                break

        if chosen is None:
            # No matching embedded sub — ship the bare video.
            _run_ffmpeg(["-i", str(video), "-c", "copy"], out, "torrent mux")
            video.unlink(missing_ok=True)
            return

        # Mux the chosen subtitle track into the mp4 as mov_text.
        args = [
            "-i", str(video),
            "-map", "0:v", "-map", "0:a?",
            "-map", f"0:s:{chosen}",
            "-c", "copy", "-c:s", "mov_text",
        ]
        _run_ffmpeg(args, out, "subtitle mux")
        video.unlink(missing_ok=True)

    def _download_torrent(self, client: str, magnet: str, workdir: Path,
                          on_progress: Callable[[float], None],
                          should_cancel: Callable[[], bool] | None) -> Path | None:
        """Run the torrent client and return the largest video file produced."""
        if client == "aria2c":
            return self._aria2_download(magnet, workdir, on_progress, should_cancel)
        return self._libtorrent_download(magnet, workdir, on_progress, should_cancel)

    def _aria2_download(self, magnet: str, workdir: Path,
                        on_progress: Callable[[float], None],
                        should_cancel: Callable[[], bool] | None,
                        select_file: str | None = None) -> Path | None:
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
        ]
        if select_file:
            cmd.append(f"--select-file={select_file}")
        cmd.append(magnet)
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

    def _libtorrent_batch_download(self, torrent_path: str, workdir: Path, episode: int,
                                   on_progress: Callable[[float], None],
                                   should_cancel: Callable[[], bool] | None) -> Path | None:
        """Download only the requested episode's file from a batch torrent."""
        import libtorrent as lt

        session = lt.session({"listen_interfaces": "0.0.0.0:6881"})
        params = lt.parse_torrent_file(torrent_path)
        params.save_path = str(workdir)
        handle = session.add_torrent(params)

        # Wait for the file list, then download only the matching file.
        started = time.monotonic()
        target = None
        while target is None and time.monotonic() - started < 60:
            try:
                for idx, f in enumerate(handle.torrent_file().files()):
                    if self._file_is_episode(f.path, episode):
                        target = idx
                        break
            except Exception:
                pass
            time.sleep(0.5)
        if target is None:
            session.pause()
            raise DownloadError(f"Episode {episode} not found inside the batch.")
        for idx in range(len(handle.torrent_file().files())):
            handle.file_priority(idx, 1 if idx == target else 0)

        last_progress = -1.0
        while time.monotonic() - started < _TORRENT_STALL_SECONDS:
            if should_cancel and should_cancel():
                session.pause()
                raise Cancelled()
            status = handle.status()
            if status.progress > last_progress + 0.02:
                on_progress(status.progress)
                last_progress = status.progress
            if status.progress >= 1.0:
                break
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
