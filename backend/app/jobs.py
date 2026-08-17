"""In-memory download jobs.

A job is one batch of tracks (a playlist, an album, or a single song).
Tracks download concurrently on a small thread pool; the frontend polls
GET /api/jobs/{id} for per-track progress.

A background sweeper keeps the downloads folder from growing forever:
job directories older than DOWNLOADS_TTL_HOURS (default 24) are deleted
once their job has finished, and orphan directories from previous runs
are cleaned the same way. Whatever outlives that pass is then held under
MAX_DOWNLOADS_GB by evicting the oldest jobs first.

Either limit can be switched off with 0, and a self-hosted instance
downloading into a folder someone actually browses usually switches both
off — the sweeper exists because a public server's disk is shared with
strangers, which is not true of a laptop or a NAS.
"""

import logging
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("unstream.jobs")

from . import analytics, downloader
from .models import Track

DOWNLOADS_DIR = Path(__file__).resolve().parent.parent / "downloads"

# 0 keeps finished downloads forever. The default suits a server whose disk
# is shared with strangers; it is the wrong default for someone downloading
# to their own machine, which is why it is the first thing self-hosters set.
DOWNLOADS_TTL_HOURS = float(os.getenv("DOWNLOADS_TTL_HOURS", "24"))

# Downloaded media are temporary. Two retention clocks: how long a finished
# file stays after the user actually fetched it (DELIVERED), and how long a
# finished file stays if the user never fetched it (DOWNLOAD). Intermediates
# (part files, torrent control files, subtitle sidecars) are deleted as soon
# as the job is finished — they are never "delivered". A running job's files
# are never touched by either clock.
#
# Short defaults are intentional: a public server must not accumulate media.
DOWNLOAD_RETENTION_HOURS = float(os.getenv("DOWNLOAD_RETENTION_HOURS", "1"))
DELIVERED_RETENTION_HOURS = float(os.getenv("DELIVERED_RETENTION_HOURS", "0.25"))

# The TTL alone is not a disk limit. Three workers can land on the order of
# 500 tracks an hour, and nothing is deleted for a day — enough to fill a
# small VPS long before the first job expires. This is the actual ceiling:
# over it, the sweeper evicts finished jobs oldest-first until it is back
# under, so the volume trades history for staying writable. 0 disables it,
# on the same reasoning as the TTL above.
DOWNLOADS_MAX_BYTES = int(float(os.getenv("MAX_DOWNLOADS_GB", "20")) * 1024**3)

# Refuse to start a job when fewer than this many MiB are free on the
# download volume, rather than letting a 700 MB episode fill the disk.
MIN_FREE_DISK_MB = int(float(os.getenv("MIN_FREE_DISK_MB", "2048")))

# Ten minutes, not an hour: a budget checked hourly can be exceeded for an
# hour. The sweep is a stat() per file, so running it often is cheap.
_SWEEP_INTERVAL_SECONDS = 600

# Be polite to YouTube: a few tracks at a time, not the whole playlist.
#
# Raising this is the obvious way to make a long discography finish sooner,
# and it is also the fastest way to get bot-checked — the limit being bought
# is how many requests one address makes at once, which is the signal being
# watched. A home connection has more room here than a datacenter one, but
# it is still the thing that breaks first, so move it in small steps.
DOWNLOAD_WORKERS = max(1, int(os.getenv("DOWNLOAD_WORKERS", "3")))
_executor = ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS)

# Anime episodes are 100-700MB each — the same concurrency that politely
# downloads a few songs would hammer a scraper site or the Telegram bot.
# Video jobs use their own, smaller pool.
ANIME_DOWNLOAD_WORKERS = max(1, int(os.getenv("ANIME_DOWNLOAD_WORKERS", "2")))
_anime_executor = ThreadPoolExecutor(max_workers=ANIME_DOWNLOAD_WORKERS)


SETTLED = ("done", "error", "cancelled")


class DiskFullError(Exception):
    """Not enough free disk to start a download (MIN_FREE_DISK_MB)."""


@dataclass
class TrackState:
    track: Track
    filename: str  # unique stem within the job, no extension
    # queued | searching | downloading | tagging | retrying | done | error | cancelled
    status: str = "queued"
    progress: float = 0.0
    error: str | None = None
    file_path: Path | None = None
    # Anime only: which provider actually served the episode (after any
    # fallback), and the actual video resolution served — probed from the
    # finished file, never the requested quality. None for audio tracks.
    provider: str | None = None
    served_quality: str | None = None
    # Anime only: live provider-chain search progress while `status` is
    # "searching" — {"checked": int, "total": int, "current": str|None} from
    # the downloader's real provider execution. None once searching ends.
    provider_progress: dict | None = None
    # True once the client fetched this track's file (or the job's ZIP).
    # Delivered files live on a shorter retention clock — the user has the
    # bytes, so the server copy can go sooner.
    delivered: bool = False

    def as_dict(self) -> dict:
        return {
            "id": self.track.id,
            "status": self.status,
            "progress": round(self.progress, 3),
            "error": self.error,
            # "mp3" / "m4a" / "opus" — the UI labels its save link with it.
            "ext": self.file_path.suffix.lstrip(".") if self.file_path else None,
            "provider": self.provider,
            "served_quality": self.served_quality,
            "provider_progress": self.provider_progress,
        }


@dataclass
class Job:
    id: str
    name: str
    quality: str = downloader.DEFAULT_QUALITY
    # Whether finished files get lyrics embedded in their tags. The UI sets
    # this per job from a global preference, the same way it picks quality.
    embed_lyrics: bool = True
    # Opaque client key (an IP, from app.limits), only for counting a caller's
    # jobs in flight. Never leaves the process — as_dict() omits it, and job
    # ids stay unguessable so anyone holding one can still fetch it.
    owner: str = ""
    # Analytics only: a hashed, daily-rotating pseudonym, so a finished track
    # can be attributed without the download pipeline ever seeing an address.
    visitor: str = ""
    tracks: dict[str, TrackState] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    # Set once, by cancel(). An Event rather than a bool under `lock` so a
    # worker can ask mid-transfer, from inside a progress hook, without
    # queueing behind whatever else is writing track state.
    stopped: threading.Event = field(default_factory=threading.Event)

    @property
    def dir(self) -> Path:
        return DOWNLOADS_DIR / self.id

    @property
    def finished(self) -> bool:
        with self.lock:
            return all(s.status in SETTLED for s in self.tracks.values())

    def as_dict(self) -> dict:
        with self.lock:
            states = [s.as_dict() for s in self.tracks.values()]
        done = sum(1 for s in states if s["status"] == "done")
        failed = sum(1 for s in states if s["status"] == "error")
        cancelled = sum(1 for s in states if s["status"] == "cancelled")
        return {
            "id": self.id,
            "name": self.name,
            "quality": self.quality,
            "tracks": states,
            "done": done,
            "failed": failed,
            "cancelled": cancelled,
            "total": len(states),
            "finished": done + failed + cancelled == len(states),
        }


_jobs: dict[str, Job] = {}


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def live_counts() -> dict:
    """What the process is doing right now — for the admin dashboard."""
    snapshot = list(_jobs.values())
    running = [job for job in snapshot if not job.finished]
    return {
        "active_jobs": len(running),
        "active_tracks": sum(
            1
            for job in running
            for state in list(job.tracks.values())
            if state.status not in SETTLED
        ),
        "jobs_tracked": len(snapshot),
    }


def active_count(owner: str) -> int:
    """How many of this client's jobs are still running."""
    # list() so a concurrent start() resizing the dict can't break iteration.
    return sum(1 for job in list(_jobs.values()) if job.owner == owner and not job.finished)


_AUDIO_HOSTS = (
    ("youtu", "youtube"),
    ("soundcloud", "soundcloud"),
)


def _host_of(url: str) -> str:
    for needle, name in _AUDIO_HOSTS:
        if needle in url:
            return name
    return "other"


def _run_track(job: Job, state: TrackState) -> None:
    if job.stopped.is_set():
        # Cancelled while this one sat in the pool's queue. cancel() has
        # already written the status; there is nothing to do but not start.
        return

    def on_progress(stage: str, fraction: float) -> None:
        with job.lock:
            # Reporting a stage after cancel() has settled this track would
            # walk it back out of a terminal state, and the UI would show a
            # cancelled download carrying on.
            if job.stopped.is_set():
                return
            state.status = stage
            state.progress = fraction

    # Which upload the download settled on, and on which try — the pipeline
    # can fall back through YouTube search to SoundCloud, so neither is
    # knowable from the outside until it happens.
    chosen = {"url": "", "attempt": 0}

    def on_source(url: str, attempt: int) -> None:
        chosen.update(url=url, attempt=attempt)

    def on_provider_progress(checked: int, total: int, current: str | None) -> None:
        with job.lock:
            if job.stopped.is_set():
                return
            state.provider_progress = {
                "checked": checked,
                "total": total,
                "current": current,
            }

    label = f"{', '.join(state.track.artists)} - {state.track.title}"
    started = time.monotonic()
    # The video pipeline reports which provider served the episode and the
    # actual resolution (ffprobe'd); audio leaves it empty.
    meta: dict = {}
    try:
        path = downloader.download_track(
            state.track,
            job.dir,
            on_progress,
            filename=state.filename,
            quality=job.quality,
            on_source=on_source,
            embed_lyrics=job.embed_lyrics,
            should_cancel=job.stopped.is_set,
            meta=meta,
            on_provider_progress=on_provider_progress,
        )
        if job.stopped.is_set():
            # Finished in the window between the cancel landing and the last
            # check inside the pipeline. Keeping it would mean a job answering
            # "cancelled" and then handing out one more file than it reported.
            path.unlink(missing_ok=True)
            return
        with job.lock:
            state.status = "done"
            state.progress = 1.0
            state.file_path = path
            state.provider = meta.get("provider")
            state.served_quality = meta.get("served_quality")
            state.provider_progress = None
        analytics.record(
            "track_done",
            visitor=job.visitor or None,
            source=_host_of(chosen["url"]),
            detail=job.quality,
            label=label,
            value=chosen["attempt"],
            ms=int((time.monotonic() - started) * 1000),
        )
    except downloader.Cancelled:
        # cancel() writes this status too, and whichever gets there first wins
        # the same value. Not an error, and not counted as one: nothing failed.
        with job.lock:
            state.status = "cancelled"
            state.progress = 0.0
    except Exception as exc:  # any failure marks just this track, not the job
        with job.lock:
            state.status = "error"
            state.error = str(exc)
            # The pipeline writes provider/served_quality into `meta` before it
            # can fail (a mislabeled release is probed, then refused), so a
            # failed track still reports what was actually served. Audio leaves
            # `meta` empty and keeps both None.
            state.provider = meta.get("provider")
            state.served_quality = meta.get("served_quality")
            state.provider_progress = None
        analytics.record(
            "track_error",
            visitor=job.visitor or None,
            source=_host_of(chosen["url"]),
            detail=analytics.error_class(str(exc)),
            label=label,
            value=chosen["attempt"],
            ms=int((time.monotonic() - started) * 1000),
        )


def _check_disk() -> None:
    """Refuse to start into a nearly-full filesystem.

    Bails out before any provider fetch or worker is spawned: a 700 MB episode
    must not be the thing that finally tops the disk, and a user failing at
    download time gets a reason instead of the server silently filling up.
    When MIN_FREE_DISK_MB <= 0 the check is off.
    """
    if MIN_FREE_DISK_MB <= 0 or not DOWNLOADS_DIR.exists():
        return
    free_bytes = shutil.disk_usage(DOWNLOADS_DIR).free
    if free_bytes < MIN_FREE_DISK_MB * 1024**2:
        raise DiskFullError(
            f"Not enough free disk to start a download "
            f"({free_bytes // 1024**2} MiB free, "
            f"need {MIN_FREE_DISK_MB} MiB)."
        )


def start(
    name: str,
    tracks: list[Track],
    quality: str = downloader.DEFAULT_QUALITY,
    embed_lyrics: bool = True,
    owner: str = "",
    visitor: str = "",
) -> Job:
    _check_disk()
    job = Job(
        id=uuid.uuid4().hex[:12],
        name=name,
        quality=quality,
        embed_lyrics=embed_lyrics,
        owner=owner,
        visitor=visitor,
    )
    # Two different tracks can share "Artist - Title" (playlist duplicates,
    # remastered copies). Concurrent downloads to one filename truncate each
    # other mid-conversion, so make every stem unique up front.
    used: set[str] = set()
    for track in tracks:
        base = downloader.safe_filename(
            f"{', '.join(track.artists)} - {track.title}"
        )
        stem, n = base, 2
        while stem.lower() in used:
            stem = f"{base} ({n})"
            n += 1
        used.add(stem.lower())
        job.tracks[track.id] = TrackState(track=track, filename=stem)
    _jobs[job.id] = job
    # A video job goes on its own pool — see ANIME_DOWNLOAD_WORKERS. Everything
    # else (audio) uses the shared one.
    pool = _anime_executor if tracks and tracks[0].media == "video" else _executor
    for state in job.tracks.values():
        pool.submit(_run_track, job, state)
    return job


def cancel(job: Job) -> int:
    """Stop every track that hasn't settled. Returns how many were stopped.

    The status is written here rather than left to the workers, so the job
    reports "cancelled" on the very next poll: a track stuck in a provider
    search cannot be interrupted mid-request and may take another few seconds
    to notice, and a button that does nothing visible for that long reads as
    broken. Whatever a worker is holding when it does notice is thrown away —
    files included — so the counts the job reported stay true.

    Tracks already finished keep their files. Cancelling an album halfway is
    "stop here", not "undo"; the finished songs stay downloadable until the
    sweeper takes them like any other job's.
    """
    job.stopped.set()
    stopped = 0
    with job.lock:
        for state in job.tracks.values():
            if state.status in SETTLED:
                continue
            state.status = "cancelled"
            state.progress = 0.0
            stopped += 1
    return stopped


def _measure(path: Path) -> tuple[float, int] | None:
    """(mtime of the newest file, total bytes) for one job directory."""
    try:
        newest, total = path.stat().st_mtime, 0
        for child in path.iterdir():
            stat = child.stat()
            newest = max(newest, stat.st_mtime)
            total += stat.st_size
    except OSError:
        return None  # vanished under us, or unreadable — leave it alone
    return newest, total


# File names that are intermediates — produced for a download and never the
# finished deliverable. Anything carrying one of these is removed once its job
# is finished, without waiting for the retention clock. A user's own files
# (upstream downloads, cover art they placed here) are never matched: the
# sweep only walks the app's per-job directories.
_INTERMEDIATE_MARKERS = (
    ".nyaatmp",   # Nyaa torrent work dir
    ".aria2",      # libtorrent/aria2 control file
    "aria2.log",   # torrent client log
    ".uue",        # misc partial
)
_INTERMEDIATE_SUFFIXES = (".part", ".srt", ".vtt")

_MAX_LOG_LINES_PER_SWEEP = 200
_sweep_logged = 0


def _is_intermediate(path: Path) -> bool:
    """A file or directory produced in the middle of a download, never meant
    to be handed to the user."""
    name = path.name.lower()
    if any(mark in name for mark in _INTERMEDIATE_MARKERS):
        return True
    if path.is_file() and path.suffix.lower() in _INTERMEDIATE_SUFFIXES:
        return True
    return False


def _evict(path: Path, reason: str = "expired") -> None:
    """Remove one job directory (or orphaned intermediate) and log it.

    Idempotent: re-running after a partial failure removes nothing extra, and
    a path vanished under us is not an error. Logs the freed bytes and, for a
    whole job directory, its age so the retention behaviour is auditable.
    """
    global _sweep_logged
    freed = 0
    if path.is_dir():
        for child in path.rglob("*"):
            try:
                freed += child.stat().st_size
            except OSError:
                pass
    else:
        try:
            freed = path.stat().st_size
        except OSError:
            freed = 0
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    if _sweep_logged < _MAX_LOG_LINES_PER_SWEEP:
        _sweep_logged += 1
        log.info(
            "cleanup: removed %s reason=%s freed_bytes=%d",
            str(path), reason, freed,
        )
    _jobs.pop(path.name, None)


def _mark_all_delivered(job: "Job") -> None:
    """A ZIP served the whole job — every finished track counts as delivered."""
    with job.lock:
        for state in job.tracks.values():
            if state.status == "done" and state.file_path:
                state.delivered = True


def mark_delivered(job: "Job", track_id: str) -> None:
    """Record that one finished track's file was fetched by the client."""
    state = job.tracks.get(track_id)
    if state is not None and state.status == "done":
        state.delivered = True


def _sweep(
    ttl_hours: float = DOWNLOADS_TTL_HOURS, max_bytes: int = DOWNLOADS_MAX_BYTES
) -> int:
    """Delete finished job directories by the retention clocks, then any
    excess over the disk budget.

    `ttl_hours` is kept for backward compatibility and treated as a hard
    upper bound: a finished job older than it is always removed. The newer,
    finer-grained clocks are the primary behaviour:

      * delivered files (fetch + user fetched them) -> DELIVERED_RETENTION_HOURS
      * finished-but-undelivered                   -> DOWNLOAD_RETENTION_HOURS
      * intermediates (part files, torrent controls) -> removed once the job
        is finished, no waiting

    A running job is never touched — its directory is skipped entirely, so a
    volume held over budget by jobs in flight stays over (the concurrency and
    per-client caps are what bound that case). The sweep only ever walks the
    app's per-job directories; a stray non-directory or a folder that is not a
    job id is left alone, so user files placed in the download root survive.

    With every limit off (0/0/0) the directory is not even walked.

    Returns how many job directories were removed.
    """
    if ttl_hours <= 0 and max_bytes <= 0 and (
        DOWNLOAD_RETENTION_HOURS <= 0 and DELIVERED_RETENTION_HOURS <= 0
    ):
        return 0
    if not DOWNLOADS_DIR.exists():
        return 0

    # Fresh cap each pass — a sweep truncated by the log limit must not
    # silence the next one's audit trail forever.
    global _sweep_logged
    _sweep_logged = 0

    now = time.time()
    removed = 0
    # (newest mtime, bytes, path) for everything that survived retention.
    survivors: list[tuple[float, int, Path]] = []

    for path in DOWNLOADS_DIR.iterdir():
        if not path.is_dir() or not path.name.isalnum():
            # A stray non-directory (a user's file dropped in the root, a
            # half-swept left-over) is out of scope: never delete something
            # the app did not generate inside a named job folder.
            continue
        job = _jobs.get(path.name)
        if job and not job.finished:
            continue  # never pull files out from under a running job

        finished = job is None or job.finished
        # A finished job's intermediates are always disposable. Delivered vs
        # undelivered final files get their own clocks.
        measured = _measure(path)
        if measured is None:
            continue
        newest, size = measured

        if not finished:
            # A job that is still finishing (status not settled) is untouched.
            survivors.append((newest, size, path))
            continue

        # A finished job's working files (the Nyaa torrent control dir, .part
        # files, subtitle sidecars) are deleted right away, before the retention
        # decision — a subbed or half-failed download must not keep them next
        # to the delivered media for the whole clock. Only the final file, if
        # any, is subject to retention.
        try:
            children = list(path.iterdir())
        except OSError:
            children = []
        for child in children:
            if _is_intermediate(child):
                _evict(child, "intermediate")

        # A finished job is cleaned by its own clock. The hard ttl cap wins
        # first; then the undelivered retention; then the (shorter) delivered
        # retention; and a job reduced to intermediates is gone immediately.
        base_expire = (
            now - DOWNLOAD_RETENTION_HOURS * 3600
            if DOWNLOAD_RETENTION_HOURS > 0 else None
        )
        delivered_cutoff = (
            now - DELIVERED_RETENTION_HOURS * 3600
            if DELIVERED_RETENTION_HOURS > 0 else None
        )

        if ttl_hours > 0 and newest < now - ttl_hours * 3600:
            _evict(path, "ttl")
            removed += 1
            continue
        if _intermediates_only(path) or (
            base_expire is not None and newest < base_expire
        ):
            _evict(path, "retention")
            removed += 1
            continue
        if delivered_cutoff is not None and _delivered_job(job) and newest < delivered_cutoff:
            _evict(path, "delivered")
            removed += 1
            continue

        survivors.append((newest, size, path))

    if max_bytes <= 0:
        return removed

    # Oldest first, so what goes is what someone is least likely to still want.
    total = sum(size for _, size, _ in survivors)
    survivors.sort()
    for _, size, path in survivors:
        if total <= max_bytes:
            break
        _evict(path, "over-budget")
        total -= size
        removed += 1
    return removed


def _intermediates_only(path: Path) -> bool:
    """True when a finished job directory holds nothing but intermediates.

    A half-failed download leaves only the torrent work dir or subtitle
    sidecars, which are never handed to anyone — delete them as soon as the
    job settled.
    """
    try:
        children = list(path.iterdir())
    except OSError:
        return False
    if not children:
        return True  # empty shell of a download
    return all(_is_intermediate(child) for child in children)


def _delivered_job(job: "Job | None") -> bool:
    """Did the user collect at least one finished file from this job?"""
    if job is None:
        return False  # an orphan's fate is decided by the undelivered clock
    with job.lock:
        return any(s.delivered for s in job.tracks.values() if s.status == "done")


def start_sweeper() -> None:
    """Cleanup thread on _SWEEP_INTERVAL_SECONDS; also sweeps leftovers from
    previous runs."""

    def loop() -> None:
        while True:
            try:
                _sweep()
            except Exception:
                pass  # a failed sweep must never kill the thread
            time.sleep(_SWEEP_INTERVAL_SECONDS)

    threading.Thread(target=loop, name="downloads-sweeper", daemon=True).start()
