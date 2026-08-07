"""In-memory download jobs.

A job is one batch of tracks (a playlist, an album, or a single song).
Tracks download concurrently on a small thread pool; the frontend polls
GET /api/jobs/{id} for per-track progress.

A background sweeper keeps the downloads folder from growing forever:
job directories older than DOWNLOADS_TTL_HOURS (default 24) are deleted
once their job has finished, and orphan directories from previous runs
are cleaned the same way.
"""

import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import analytics, downloader
from .models import Track

DOWNLOADS_DIR = Path(__file__).resolve().parent.parent / "downloads"

DOWNLOADS_TTL_HOURS = float(os.getenv("DOWNLOADS_TTL_HOURS", "24"))
_SWEEP_INTERVAL_SECONDS = 3600

# Be polite to YouTube: a few tracks at a time, not the whole playlist.
_executor = ThreadPoolExecutor(max_workers=3)


@dataclass
class TrackState:
    track: Track
    filename: str  # unique stem within the job, no extension
    status: str = "queued"  # queued | searching | downloading | tagging | retrying | done | error
    progress: float = 0.0
    error: str | None = None
    file_path: Path | None = None

    def as_dict(self) -> dict:
        return {
            "id": self.track.id,
            "status": self.status,
            "progress": round(self.progress, 3),
            "error": self.error,
            # "mp3" / "m4a" / "opus" — the UI labels its save link with it.
            "ext": self.file_path.suffix.lstrip(".") if self.file_path else None,
        }


@dataclass
class Job:
    id: str
    name: str
    quality: str = downloader.DEFAULT_QUALITY
    # Opaque client key (an IP, from app.limits), only for counting a caller's
    # jobs in flight. Never leaves the process — as_dict() omits it, and job
    # ids stay unguessable so anyone holding one can still fetch it.
    owner: str = ""
    # Analytics only: a hashed, daily-rotating pseudonym and which client
    # started the job, so a finished track can be attributed without the
    # download pipeline ever seeing an address.
    visitor: str = ""
    surface: str = "web"
    tracks: dict[str, TrackState] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def dir(self) -> Path:
        return DOWNLOADS_DIR / self.id

    @property
    def finished(self) -> bool:
        with self.lock:
            return all(s.status in ("done", "error") for s in self.tracks.values())

    def as_dict(self) -> dict:
        with self.lock:
            states = [s.as_dict() for s in self.tracks.values()]
        done = sum(1 for s in states if s["status"] == "done")
        failed = sum(1 for s in states if s["status"] == "error")
        return {
            "id": self.id,
            "name": self.name,
            "quality": self.quality,
            "tracks": states,
            "done": done,
            "failed": failed,
            "total": len(states),
            "finished": done + failed == len(states),
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
            if state.status not in ("done", "error")
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
    def on_progress(stage: str, fraction: float) -> None:
        with job.lock:
            state.status = stage
            state.progress = fraction

    # Which upload the download settled on, and on which try — the pipeline
    # can fall back through YouTube search to SoundCloud, so neither is
    # knowable from the outside until it happens.
    chosen = {"url": "", "attempt": 0}

    def on_source(url: str, attempt: int) -> None:
        chosen.update(url=url, attempt=attempt)

    label = f"{', '.join(state.track.artists)} - {state.track.title}"
    started = time.monotonic()
    try:
        path = downloader.download_track(
            state.track,
            job.dir,
            on_progress,
            filename=state.filename,
            quality=job.quality,
            on_source=on_source,
        )
        with job.lock:
            state.status = "done"
            state.progress = 1.0
            state.file_path = path
        analytics.record(
            "track_done",
            surface=job.surface,
            visitor=job.visitor or None,
            source=_host_of(chosen["url"]),
            detail=job.quality,
            label=label,
            value=chosen["attempt"],
            ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:  # any failure marks just this track, not the job
        with job.lock:
            state.status = "error"
            state.error = str(exc)
        analytics.record(
            "track_error",
            surface=job.surface,
            visitor=job.visitor or None,
            source=_host_of(chosen["url"]),
            detail=analytics.error_class(str(exc)),
            label=label,
            value=chosen["attempt"],
            ms=int((time.monotonic() - started) * 1000),
        )


def start(
    name: str,
    tracks: list[Track],
    quality: str = downloader.DEFAULT_QUALITY,
    owner: str = "",
    visitor: str = "",
    surface: str = "web",
) -> Job:
    job = Job(
        id=uuid.uuid4().hex[:12],
        name=name,
        quality=quality,
        owner=owner,
        visitor=visitor,
        surface=surface,
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
    for state in job.tracks.values():
        _executor.submit(_run_track, job, state)
    return job


def _sweep(ttl_hours: float = DOWNLOADS_TTL_HOURS) -> int:
    """Delete expired job directories; returns how many were removed."""
    if not DOWNLOADS_DIR.exists():
        return 0
    cutoff = time.time() - ttl_hours * 3600
    removed = 0
    for path in DOWNLOADS_DIR.iterdir():
        if not path.is_dir():
            continue
        job = _jobs.get(path.name)
        if job and not job.finished:
            continue  # never pull files out from under a running job
        try:
            newest = max(
                (p.stat().st_mtime for p in path.iterdir()),
                default=path.stat().st_mtime,
            )
        except OSError:
            continue
        if newest < cutoff:
            shutil.rmtree(path, ignore_errors=True)
            _jobs.pop(path.name, None)
            removed += 1
    return removed


def start_sweeper() -> None:
    """Hourly cleanup thread; also sweeps leftovers from previous runs."""

    def loop() -> None:
        while True:
            try:
                _sweep()
            except Exception:
                pass  # a failed sweep must never kill the thread
            time.sleep(_SWEEP_INTERVAL_SECONDS)

    threading.Thread(target=loop, name="downloads-sweeper", daemon=True).start()
