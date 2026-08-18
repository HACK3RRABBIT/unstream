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
from .providers import EpisodeSource, EpisodeStream, QualityUnavailable

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


# Subtitle codecs that carry text and can be muxed into mov_text (what the
# mp4 muxer writes). Everything else — dvd_subtitle, hdmv_pgs_subtitle, dvb_subtitle,
# xsub, ... — is a bitmap (picture) subtitle that ffmpeg refuses to re-encode to
# text, so a fansub with only such a track must ship a bare video rather than
# fail the download over a subtitle that cannot be carried.
_TEXT_SUB_CODECS = {
    "subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text",
    "microdvd", "realtext", "subviewer", "subviewer1", "sami", "stl",
}

# A resolution marker in a release title. `p` after 3-4 digits (480p, 1080p)
# is the unambiguous form; dimensions (1280x720, 854×480) are the other. The
# leading `(?<![0-9])` stops "1480p"/"48000" from matching inside a longer
# number, and the trailing `(?![0-9])` on the dimension stops "001-4801".
_RES_TAG = re.compile(r"(?<![0-9])(\d{3,4})p(?!\d)", re.IGNORECASE)
_DIM_TAG = re.compile(r"(?<![0-9])(\d{3,4})\s*[x×]\s*(480|720|1080)(?!\d)", re.IGNORECASE)

# A multi-episode range in a release title: two episode-like numbers joined by
# a dash, en/em dash or tilde, spaces allowed around the separator (001 ~ 079,
# 001-079, E01–E06). Either endpoint may carry an E/EP/Episode marker. The
# digit boundaries keep it from matching inside a longer number, a width
# (1280x720), or a hash.
_RANGE_RE = re.compile(
    r"(?<!\d)(?:EP|Ep|Episode|E)?\s*(\d{1,4})\s*[-–—~]\s*"
    r"(?:EP|Ep|Episode|E)?\s*(\d{1,4})(?!\d)",
    re.IGNORECASE,
)
# An explicit batch label — a multi-episode signal even when the range can't be
# parsed out of the title (Show 001 [BATCH]).
_BATCH_TAG_RE = re.compile(r"\bbatch\b", re.IGNORECASE)

# A multi-episode *list* with no range separator — "Show 001 002 003" or
# "Show E01 E02" — as opposed to a single episode beside a metadata number
# ("Show 01 720p"). Conservative on purpose: only zero-padded episode forms
# (001, 02, 010) and explicit E/EP markers count, and only when two stand
# adjacent (whitespace/comma apart). A year (2001), a resolution (720p), a
# bare number, or an SxxExx marker never does. A missed batch falls through
# to the next provider; a false positive would break a normal single-episode
# download, so it errs toward single.
_MULTI_EP_LIST_RE = re.compile(
    r"(?<!\d)(?:0\d{1,3}|(?:EP|Ep|Episode|E)\s*\d{1,4})(?!\d)"
    r"(?:\s+|\s*,\s*)"
    r"(?<!\d)(?:0\d{1,3}|(?:EP|Ep|Episode|E)\s*\d{1,4})(?!\d)"
)


def _title_resolution(title: str) -> str | None:
    """The resolution a release title clearly claims, or None.

    Returns "480"/"720"/"1080"/"2160"... (the digits before a `p`), or the
    height half of a width×height tag. Explicitly NOT a loose substring: an
    audio bitrate like "48000" or an episode range like "001-480" carries no
    `p` and is not a resolution. A title with no explicit marker returns
    None, so a caller asking for a specific quality never accepts it.
    """
    m = _RES_TAG.search(title)
    if m:
        return m.group(1)
    m = _DIM_TAG.search(title)
    if m:
        width, height = int(m.group(1)), int(m.group(2))
        # A plausible frame for that height: 16:9 (854/1280/1920) or 4:3
        # (720/960/1440). Guards against junk like a random "1080" digit run.
        if 1.0 <= width / height <= 2.1:
            return m.group(2)
    return None


def _best_seeded(candidates: dict[str, dict]) -> dict | None:
    """Pick the best-scored candidate: a true single-episode marker adds 200."""
    best = None
    for t in candidates.values():
        score = t["seeders"] + (200 if t.get("explicit") else 0)
        if best is None or score > best["score"]:
            best = {**t, "score": score}
    return best


def _episode_ranges(title: str) -> list[tuple[int, int]]:
    """Episode ranges a release title claims, as (start, end) pairs.

    Nyaa batches are titled with the range of episodes they hold
    (001-079, 001 ~ 079, E01–E06), so a requested episode anywhere in that
    range — first, middle or last — belongs to the batch and must be extracted
    from it, never pulled as a whole multi-GiB download. Both endpoints are
    parsed so a mid-range episode whose number never appears in the title is
    still recognized as part of the batch.
    """
    out = []
    for start, end in _RANGE_RE.findall(title):
        lo, hi = int(start), int(end)
        out.append((lo, hi) if lo <= hi else (hi, lo))
    return out


def _multi_episode_space_list(title: str) -> bool:
    """Does `title` list two or more episodes as a space/comma-separated run
    (Show 001 002 003, Show E01 E02) with no range separator?

    Only very strong evidence counts: two adjacent, standalone, zero-padded
    episode numbers (01/001/010) or E/EP markers. A single episode next to a
    metadata number (Show 01 720p, Show 01 1080p) is NOT a batch — the second
    number is a resolution, not an episode — and neither are years or SxxExx
    markers. Conservative on purpose: a missed batch can fall through to the
    next provider, while a false positive would treat a normal single-episode
    release as a batch and break its download.
    """
    return bool(_MULTI_EP_LIST_RE.search(title))


class NyaaProvider:
    name = "nyaa"
    # The provider fetches the torrent itself (torrents aren't an HLS url), so
    # the downloader must call download() rather than yt-dlp on a url.
    streams_hls = False

    def available(self) -> bool:
        return True  # keyless and free; no credentials

    def resolve(
        self, title: str, year: int | None, anilist_id: int | None = None
    ) -> EpisodeSource:
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
        search).

        For an explicit quality (480/720/1080) only torrents whose title
        clearly claims that resolution are eligible; if none exist this raises
        QualityUnavailable rather than silently picking another resolution.
        `original` (or an empty/unknown quality) keeps the best-seeded
        behavior: torrents carry whatever resolution the fansub released.
        """
        title = src.anime_id
        queries = []
        if src.season > 0:
            queries.append(f"{title} S{src.season:02d}E{episode:02d}")
        queries.append(f"{title} {episode}")

        wanted = quality if quality in ("480", "720", "1080") else ""

        singles: dict[str, dict] = {}
        batches: dict[str, dict] = {}
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

            page_singles, page_batches = self._parse_rows(resp.text, episode)
            for t in page_singles:
                singles.setdefault(t["torrent_id"], t)
            for t in page_batches:
                batches.setdefault(t["torrent_id"], t)

        # An explicit request accepts only releases that claim that resolution.
        if wanted:
            singles = {
                k: t
                for k, t in singles.items()
                if _title_resolution(t["title"]) == wanted
            }
            batches = {
                k: t
                for k, t in batches.items()
                if _title_resolution(t["title"]) == wanted
            }

        best = _best_seeded(singles)
        best_batch = _best_seeded(batches)
        if (best is None or best["seeders"] == 0) and best_batch is not None:
            best = {**best_batch, "batch": True}
        if best is None or best["seeders"] == 0:
            if wanted:
                raise QualityUnavailable(
                    f"No {wanted}p release of '{title}' episode {episode} on Nyaa."
                )
            raise ProviderError(
                f"No seeded torrent containing '{title}' episode {episode} found on Nyaa."
            )
        return best

    @staticmethod
    def _parse_rows(html: str, episode: int) -> tuple[list[dict], list[dict]]:
        """Parse a Nyaa search page into (single-episode rows, batch rows).

        Every row naming the episode is returned (not just the best), so the
        caller can filter by resolution before choosing. An episode number is
        "real" when it appears with a delimiter or marker (EP 01, E01, - 001,
        (001), 001 [) — never as bare digits that could be a hash, a year, or
        a resolution. A row is a batch when it holds more than one episode: a
        range (001-574, E01-E06, 001 ~ 079, first/middle/last covered), an
        explicit `[BATCH]` label, or a space/comma-separated episode list
        (001 002 003, E01 E02). Batches are kept as an extractable fallback —
        a single episode is always extracted, never the whole multi-GiB torrent.
        """
        # An episode number is "real" when it appears with a delimiter or
        # marker (EP 01, E01, - 001, (001), 001 [) — never as bare digits
        # that could be a hash, a year, or a resolution.
        ep_re = re.compile(
            rf"(?:EP|Ep|Episode|E|#)\s*0*{episode}(?!\d)"
            rf"|(?:^|[\s\-–—(\[])\s*0*{episode}\s*(?=[\]\s\-–—,]|$)"
        )
        marker = r"(?:EP|Ep|Episode|E|#)\s*0*%d(?!\d)" % episode

        rows = re.findall(r"<tr[^>]*>.*?</tr>", html, re.S)
        singles: list[dict] = []
        batches: list[dict] = []
        for row in rows[1:]:  # first row is the header
            title_m = re.search(r'href="/view/\d+"[^>]*title="([^"]+)"', row)
            magnet = re.search(r'href="(magnet:\?[^"]+)"', row)
            view_m = re.search(r'href="(/view/\d+)"', row)
            seeders_m = re.search(r'class="text-center"[^>]*>(\d+)<', row)
            size_m = re.search(r"([\d.]+\s+(?:GiB|MiB))", row)
            if not title_m or not magnet:
                continue
            title = title_m.group(1)
            ranges = _episode_ranges(title)
            if not (ep_re.search(title) or any(lo <= episode <= hi for lo, hi in ranges)):
                continue
            seeders = int(seeders_m.group(1)) if seeders_m else 0
            common = {
                "title": title,
                "magnet": magnet.group(1),
                "torrent_id": (view_m.group(1) if view_m else "").rsplit("/", 1)[-1],
                "seeders": seeders,
                "size": size_m.group(1) if size_m else "",
            }
            if ranges or _BATCH_TAG_RE.search(title) or _multi_episode_space_list(title):
                batches.append(common)
            else:
                singles.append({**common, "explicit": bool(re.search(marker, title))})
        return singles, batches

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
        subs: list[str] | None = None,
    ) -> Path:
        """Download the torrent, extract the video, return it as mp4.

        `dest` is a stem (no extension). Anime fansubs embed soft subtitle
        tracks inside the mkv; the requested languages are muxed into the mp4
        (mov_text) so they can be toggled in any player. `subs` is a list of
        "eng"/"fas"; empty/None keeps the video bare. Persian is generated by
        translating the embedded English track when the release has none of its
        own (never fails the download — a failed translation ships English).
        """
        magnet = stream.url
        if not magnet or not magnet.startswith("magnet:"):
            raise DownloadError("Nyaa stream has no magnet link.")

        workdir = dest.parent / f"{dest.name}.nyaatmp"
        workdir.mkdir(parents=True, exist_ok=True)

        client = _pick_torrent_client()
        try:
            # A batch torrent needs the single episode's file selected up front;
            # a single-episode torrent downloads whole.
            if stream.batch and stream.torrent_id:
                video = self._download_batch_episode(
                    client, stream, workdir, on_progress, should_cancel
                )
            else:
                video = self._download_torrent(
                    client, magnet, workdir, on_progress, should_cancel
                )
            if video is None or not video.is_file():
                raise DownloadError("No video file found in the torrent.")

            out = dest.with_name(dest.name + ".mp4")
            if video.suffix.lower() == ".mp4":
                video.rename(out)
            else:
                self._finalize(video, out, subs or [])
            return out
        except Exception:
            import traceback

            raise DownloadError(
                f"Nyaa download failed: {traceback.format_exc(limit=6)}"
            ) from None
        finally:
            # Clean the torrent working directory either way.
            shutil.rmtree(workdir, ignore_errors=True)

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
            target_rel = None
            for line in listing.stdout.splitlines():
                # aria2 1.37 prints each file as TWO lines — "idx|path" then
                # "|length" — where the length-only line has an empty index.
                # Only a line whose first field is a numeric file index names
                # a file; the path is validated by _file_is_episode. This also
                # accepts the older single-line "idx|path|length" form.
                parts = line.split("|")
                if (
                    len(parts) >= 2
                    and parts[0].strip().isdigit()
                    and self._file_is_episode(parts[1], stream.episode)
                ):
                    target_idx = parts[0].strip()
                    target_rel = self._batch_rel_path(parts[1])
                    break
            if target_idx is None:
                raise DownloadError(
                    f"Episode {stream.episode} not found inside the batch."
                )
            self._aria2_download(
                str(torrent_file), workdir, on_progress, should_cancel,
                select_file=target_idx,
            )
            # aria2 preallocates every file in the torrent, so unselected files
            # sit in the workdir as zero-filled look-alikes — _largest_video
            # would pick the wrong (empty) one. Return the exact file that
            # --select-file downloaded; never fall back to another episode.
            video = workdir / target_rel
            if not video.is_file():
                raise DownloadError(
                    f"Episode {stream.episode} was not downloaded from the batch."
                )
            return video
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
    def _batch_rel_path(raw: str) -> Path:
        """The workdir-relative path of a torrent file, from aria2's listing.

        aria2 prints "./folder/file"; strip the leading "." component and
        refuse anything that could escape the working directory (absolute
        paths, `..` components).
        """
        p = Path(raw.strip())
        if p.parts and p.parts[0] == ".":
            p = Path(*p.parts[1:])
        if p.is_absolute() or ".." in p.parts:
            raise DownloadError("batch file path escapes the working directory")
        return p

    @staticmethod
    @staticmethod
    def _find_sub_stream(video: Path, language: str) -> str | None:
        """The per-type index of the embedded subtitle stream whose language
        tag matches `language` ("eng"/"fas"), or None.

        ffprobe reports the global stream index, but ffmpeg's `-map 0:s:N`
        wants the per-type (subtitle) index — the line's position among the
        subtitle-only probe output. Using the global index would select the
        wrong subtitle (or none) whenever English isn't the first track.

        Bitmap subtitle streams (dvd_subtitle, hdmv_pgs_subtitle, ...) cannot
        be muxed into mov_text — ffmpeg refuses "subtitle encoding currently
        only possible from text to text or bitmap to bitmap". A track that
        can't be carried is returned as None so the caller falls back to a
        bare video instead of failing the whole download.
        """
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "s",
                "-show_entries", "stream=index,codec_name:stream_tags=language,title",
                "-of", "csv=p=0", str(video),
            ],
            capture_output=True, text=True, timeout=30,
        )
        for sub_idx, line in enumerate(probe.stdout.strip().splitlines()):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            # parts[0] is the global index, parts[1] the codec, parts[2] the
            # language code; parts[3:] is the title, which a real fansub
            # carries ("2,subrip,eng,English") — join so a code-free track
            # whose title names the language still matches.
            if parts[1].lower() not in _TEXT_SUB_CODECS:
                continue  # bitmap subtitle: cannot become mov_text
            lang = parts[2].lower()
            title = " ".join(parts[3:]).lower()
            if language == "eng" and (
                lang in ("eng", "en") or (not lang and re.search(r"\benglish?\b", title))
            ):
                return str(sub_idx)
            if language == "fas" and (
                lang in ("fas", "fa", "per")
                or (not lang and re.search(r"\bpersian\b|\bfarsi\b", title))
            ):
                return str(sub_idx)
        return None

    @staticmethod
    def _mux_embedded_and_srt(video: Path, out: Path,
                              embedded: list[tuple[str, str]],
                              srt_files: list[tuple[str, Path]]) -> None:
        """Remux a video with a mix of embedded subtitle streams and SRT files
        as soft mov_text tracks, each with its language metadata."""
        from ..downloader import _run_ffmpeg

        args = ["-i", str(video)]
        for _lang, sub in srt_files:
            args += ["-i", str(sub)]
        args += ["-c", "copy", "-map", "0:v", "-map", "0:a?"]
        slot = 0
        for idx, lang in embedded:
            args += ["-map", f"0:s:{idx}", "-c:s", "mov_text",
                     f"-metadata:s:s:{slot}", f"language={lang}"]
            slot += 1
        for k, (lang, _sub) in enumerate(srt_files, start=1):
            args += ["-map", f"{k}:0", "-c:s", "mov_text",
                     f"-metadata:s:s:{slot}", f"language={lang}"]
            slot += 1
        _run_ffmpeg(args, out, "subtitle mux")

    @staticmethod
    def _finalize(video: Path, out: Path, subs: list[str]) -> None:
        """Remux a non-mp4 video to mp4, muxing the requested subtitle tracks.

        `subs` is a list of "eng"/"fas" (empty = none). The tracks are matched
        from the file's embedded stream metadata; when the user asks for Persian
        and the release has no Persian track of its own, one is generated by
        translating the embedded English track. Subtitles are nice-to-have: any
        failure falls back to the English track or a bare video, never failing
        the download.
        """
        from ..downloader import _run_ffmpeg

        if not subs:
            _run_ffmpeg(["-i", str(video), "-c", "copy"], out, "torrent mux")
            video.unlink(missing_ok=True)
            return

        want_eng = "eng" in subs
        want_fas = "fas" in subs
        eng_idx = NyaaProvider._find_sub_stream(video, "eng")
        fas_idx = NyaaProvider._find_sub_stream(video, "fas")

        # Preserve: extract the embedded eng/fas tracks to SRT so a track that
        # didn't match the language heuristic isn't lost. The bare-video mux
        # below only ever runs when the requested languages are genuinely
        # absent, and even then the video itself is still produced.
        from .subtitle_source import extract_embedded

        embedded_srts = extract_embedded(video, out)

        if not want_fas:
            # The single-language path: mux one matching embedded track, or the
            # extracted SRT recovered from the mkv when the heuristic missed
            # it, or a bare video when the language is genuinely absent.
            if want_eng and eng_idx is not None:
                _run_ffmpeg(
                    ["-i", str(video), "-map", "0:v", "-map", "0:a?",
                     "-map", f"0:s:{eng_idx}", "-c", "copy", "-c:s", "mov_text"],
                    out, "subtitle mux",
                )
            elif want_eng and "eng" in embedded_srts:
                _run_ffmpeg(
                    ["-i", str(video), "-i", str(embedded_srts["eng"]),
                     "-map", "0:v", "-map", "0:a?", "-map", "1:0",
                     "-c", "copy", "-c:s", "mov_text",
                     "-metadata:s:s:0", "language=eng"],
                    out, "subtitle mux",
                )
            else:
                _run_ffmpeg(["-i", str(video), "-c", "copy"], out, "torrent mux")
            video.unlink(missing_ok=True)
            return

        # Persian requested. Prefer an embedded Persian track; otherwise
        # translate the embedded English track into an SRT. A failed
        # translation just drops the Persian track.
        fas_track: tuple[str, str | Path] | None = None
        if fas_idx is not None:
            fas_track = ("embedded", fas_idx)
        elif eng_idx is not None:
            eng_srt = video.with_name(video.stem + ".eng.srt")
            try:
                _run_ffmpeg(
                    ["-i", str(video), "-map", f"0:s:{eng_idx}", "-c:s", "srt"],
                    eng_srt, "subtitle extract",
                )
            except Exception:  # noqa: BLE001 — subtitles are nice-to-have
                eng_srt.unlink(missing_ok=True)
                eng_srt = None
            if eng_srt is not None:
                from .subtitle_translate import translate_srt_file

                fas_srt = translate_srt_file(
                    eng_srt, "fa", video.with_name(video.stem + ".fas.srt")
                )
                eng_srt.unlink(missing_ok=True)
                if fas_srt is not None:
                    fas_track = ("srt", fas_srt)
        # No embedded English matched the heuristic; recover it from the mkv's
        # extracted tracks so fas can still be generated from real bytes.
        if fas_track is None and eng_idx is None and "eng" in embedded_srts:
            eng_srt = embedded_srts["eng"]
            from .subtitle_translate import translate_srt_file

            fas_srt = translate_srt_file(
                eng_srt, "fa", video.with_name(video.stem + ".fas.srt")
            )
            if fas_srt is not None:
                fas_track = ("srt", fas_srt)
            embedded_srts.pop("eng", None)

        embedded: list[tuple[str, str]] = []
        srt_files: list[tuple[str, Path]] = []
        if want_eng and eng_idx is not None:
            embedded.append((eng_idx, "eng"))
        elif want_eng and "eng" in embedded_srts:
            srt_files.append(("eng", embedded_srts["eng"]))
        if fas_track is not None:
            if fas_track[0] == "embedded":
                embedded.append((str(fas_track[1]), "fas"))
            else:
                srt_files.append(("fas", fas_track[1]))  # type: ignore[arg-type]

        if not embedded and not srt_files:
            # No subtitles could be produced at all. Fall back to the available
            # English track — a failed translation must never strip the user of
            # subtitles entirely — or a bare video when there's no English.
            if not want_eng and eng_idx is not None:
                embedded.append((eng_idx, "eng"))
            if not embedded:
                _run_ffmpeg(["-i", str(video), "-c", "copy"], out, "torrent mux")
                video.unlink(missing_ok=True)
                return

        NyaaProvider._mux_embedded_and_srt(video, out, embedded, srt_files)
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
            # aria2's own cap — long enough for a large episode even on a slow
            # swarm (the job's own progress-aware stall check is stricter).
            "--bt-stop-timeout=3600",
            "--summary-interval=5",
            "--console-log-level=warn",
            "--bt-tracker=" + ",".join(_PUBLIC_TRACKERS),
        ]
        if select_file:
            cmd.append(f"--select-file={select_file}")
        cmd.append(magnet)
        # Output goes to a log file, NOT a pipe: aria2 writes summaries every
        # few seconds, and an unread pipe buffer fills in ~30s, which blocks
        # aria2 mid-transfer (a large episode never finishes).
        log_file = workdir / "aria2.log"
        try:
            with log_file.open("wb") as out:
                proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT)
        except OSError as exc:
            raise DownloadError(f"Could not run aria2c: {exc}") from exc

        # A large episode can take 30+ minutes at modest speeds, so there is no
        # stall timeout: the download runs until aria2 exits or the user
        # cancels. Completion is signalled by aria2's exit code, NOT by the
        # absence of the ".aria2" control file — aria2 can leave it after a
        # successful download. The job reports "downloading" meanwhile.
        while True:
            if should_cancel and should_cancel():
                proc.terminate()
                raise Cancelled()
            if proc.poll() is not None:
                break  # aria2 exited on its own
            time.sleep(2)
        proc.wait(timeout=300)
        if proc.returncode not in (0, -15):
            raise DownloadError("aria2c failed to download the torrent.")
        # aria2 can leave "<name>.aria2" control files after a successful
        # download; they'd make _largest_video skip the complete file, so drop
        # them now that the process has exited 0.
        for ctl in workdir.rglob("*.aria2"):
            ctl.unlink(missing_ok=True)
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
        # No stall timeout — a large episode downloads until done or cancelled.
        while not handle.is_seed():
            if should_cancel and should_cancel():
                session.pause()
                raise Cancelled()
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
        while True:
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
        # A file still being written has a "<name>.aria2" control file next to
        # it — skipping those avoids remuxing a half-downloaded episode.
        incomplete = {p.with_suffix("") for p in workdir.rglob("*.aria2") if p.is_file()}
        vids = [
            p
            for p in workdir.rglob("*")
            if p.is_file()
            and p not in incomplete
            and p.suffix.lower() in (".mp4", ".mkv", ".avi", ".webm")
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
