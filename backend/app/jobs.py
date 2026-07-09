"""In-memory download jobs.

A job is one batch of tracks (a playlist, an album, or a single song).
Tracks download concurrently on a small thread pool; the frontend polls
GET /api/jobs/{id} for per-track progress.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import downloader
from .models import Track

DOWNLOADS_DIR = Path(__file__).resolve().parent.parent / "downloads"

# Be polite to YouTube: a few tracks at a time, not the whole playlist.
_executor = ThreadPoolExecutor(max_workers=3)


@dataclass
class TrackState:
    track: Track
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
        }


@dataclass
class Job:
    id: str
    name: str
    tracks: dict[str, TrackState] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def dir(self) -> Path:
        return DOWNLOADS_DIR / self.id

    def as_dict(self) -> dict:
        with self.lock:
            states = [s.as_dict() for s in self.tracks.values()]
        done = sum(1 for s in states if s["status"] == "done")
        failed = sum(1 for s in states if s["status"] == "error")
        return {
            "id": self.id,
            "name": self.name,
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
        path = downloader.download_track(state.track, job.dir, on_progress)
        with job.lock:
            state.status = "done"
            state.progress = 1.0
            state.file_path = path
    except Exception as exc:  # any failure marks just this track, not the job
        with job.lock:
            state.status = "error"
            state.error = str(exc)


def start(name: str, tracks: list[Track]) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], name=name)
    for track in tracks:
        job.tracks[track.id] = TrackState(track=track)
    _jobs[job.id] = job
    for state in job.tracks.values():
        _executor.submit(_run_track, job, state)
    return job
