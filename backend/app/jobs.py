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

from . import downloader
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


def _run_track(job: Job, state: TrackState) -> None:
    def on_progress(stage: str, fraction: float) -> None:
        with job.lock:
            state.status = stage
            state.progress = fraction

    try:
        path = downloader.download_track(
            state.track,
            job.dir,
            on_progress,
            filename=state.filename,
            quality=job.quality,
        )
        with job.lock:
            state.status = "done"
            state.progress = 1.0
            state.file_path = path
    except Exception as exc:  # any failure marks just this track, not the job
        with job.lock:
            state.status = "error"
            state.error = str(exc)


def start(
    name: str, tracks: list[Track], quality: str = downloader.DEFAULT_QUALITY
) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], name=name, quality=quality)
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
