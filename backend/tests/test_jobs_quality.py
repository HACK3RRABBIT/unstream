"""A track's own quality wins over its job's, when it has one.

Anime is the reason this exists: a season download used to force every
episode through the same resolution because `_run_track` always passed
`job.quality` to the pipeline, no matter what a specific episode's Track
carried. Music never sets `Track.quality`, so this is invisible to it —
`state.track.quality or job.quality` degrades to plain `job.quality` whenever
the track doesn't override it.
"""

from app import downloader, jobs
from app.models import Track


def make_track(track_id: str, quality: str | None = None) -> Track:
    return Track(
        id=track_id,
        title=f"Episode {track_id}",
        artists=["Show"],
        album="Show — Season 1",
        duration_ms=1_440_000,
        cover_url=None,
        track_number=1,
        media="video",
        quality=quality,
    )


def make_job(*tracks: Track, quality: str = "720") -> jobs.Job:
    job = jobs.Job(id="j1", name="Show — Season 1", quality=quality)
    for track in tracks:
        job.tracks[track.id] = jobs.TrackState(track=track, filename=track.id)
    return job


def test_track_quality_override_wins(monkeypatch):
    job = make_job(make_track("e1", quality="1080"), quality="720")
    seen: dict = {}

    def download(track, out_dir, on_progress, **kwargs):
        seen["quality"] = kwargs["quality"]
        raise downloader.DownloadError("stop here — only the argument matters")

    monkeypatch.setattr(downloader, "download_track", download)
    jobs._run_track(job, job.tracks["e1"])

    assert seen["quality"] == "1080"  # the episode's own choice, not the job's


def test_track_without_override_falls_back_to_job_quality(monkeypatch):
    job = make_job(make_track("e1", quality=None), quality="720")
    seen: dict = {}

    def download(track, out_dir, on_progress, **kwargs):
        seen["quality"] = kwargs["quality"]
        raise downloader.DownloadError("stop here — only the argument matters")

    monkeypatch.setattr(downloader, "download_track", download)
    jobs._run_track(job, job.tracks["e1"])

    assert seen["quality"] == "720"


def test_two_episodes_in_one_job_can_each_get_their_own_resolution(monkeypatch):
    """The whole point: a season download is not stuck at one resolution for
    every episode in it."""
    job = make_job(
        make_track("e1", quality="1080"),
        make_track("e2", quality="480"),
        make_track("e3", quality=None),
        quality="720",
    )
    seen: dict[str, str] = {}

    def download(track, out_dir, on_progress, **kwargs):
        seen[track.id] = kwargs["quality"]
        raise downloader.DownloadError("stop here — only the argument matters")

    monkeypatch.setattr(downloader, "download_track", download)
    for episode_id in ("e1", "e2", "e3"):
        jobs._run_track(job, job.tracks[episode_id])

    assert seen == {"e1": "1080", "e2": "480", "e3": "720"}
