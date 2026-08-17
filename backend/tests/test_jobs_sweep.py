"""What the downloads sweeper is allowed to delete.

The limits exist because a public server's disk is shared with strangers.
Someone self-hosting has the opposite problem — they want the files they
downloaded to still be there tomorrow — so every limit has an off switch,
and 0 is the value anyone would reach for to set it. These tests pin that
0 means "keep everything" and not, as it read literally before, "everything
is older than zero hours ago, delete it all".

There are two independent mechanisms:

  * retention clocks (the primary behaviour): a finished file lives for
    DOWNLOAD_RETENTION_HOURS, or for the shorter DELIVERED_RETENTION_HOURS
    once the user actually fetched it; a job reduced to intermediates goes
    the moment it settles. These are on by default.
  * the legacy TTL (DOWNLOADS_TTL_HOURS) as a hard upper bound, and the
    MAX_DOWNLOADS_GB disk ceiling evicting oldest-first. Both default to 0.

A running job is never touched, and only the app's own per-job directories
(an alnum name) are walked — a user's file dropped in the download root is
never the sweep's business.
"""

import os
import time

import pytest

from app import jobs
from app.models import Track

HOUR = 3600


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    """A downloads directory the sweeper will walk, with no jobs in memory.

    _sweep() skips directories belonging to a job that is still running; an
    empty registry means every directory here is fair game, which is the
    case worth testing.
    """
    monkeypatch.setattr(jobs, "DOWNLOADS_DIR", tmp_path)
    monkeypatch.setattr(jobs, "_jobs", {})
    return tmp_path


@pytest.fixture
def retention_off(monkeypatch):
    """Turn the retention clocks off, isolating the legacy TTL/disk tests."""
    monkeypatch.setattr(jobs, "DOWNLOAD_RETENTION_HOURS", 0)
    monkeypatch.setattr(jobs, "DELIVERED_RETENTION_HOURS", 0)


def make_job_dir(root, name: str, *, age_hours: float = 0.0, size: int = 1024):
    """One finished job's folder, aged into the past."""
    path = root / name
    path.mkdir()
    (path / "track.mp3").write_bytes(b"\0" * size)
    when = time.time() - age_hours * HOUR
    # _measure() takes the newest mtime in the folder, so the file has to be
    # aged too — ageing only the directory would leave the file dated now.
    for target in (path / "track.mp3", path):
        os.utime(target, (when, when))
    return path


def make_intermediate_job_dir(root, name: str):
    """A finished job folder holding nothing but intermediates."""
    path = root / name
    path.mkdir()
    (path / "ep.part").write_bytes(b"\0" * 10)
    return path


def done_job(jid: str, delivered: bool = False) -> jobs.Job:
    """An in-memory finished job (all tracks settled) for one folder."""
    job = jobs.Job(id=jid, name="x")
    state = jobs.TrackState(
        track=Track(id="t1", title="T", artists=["A"], album="Al",
                    duration_ms=1, cover_url=None),
        filename="track",
        status="done",
    )
    state.delivered = delivered
    job.tracks["t1"] = state
    return job


# ── retention clocks (the primary behaviour) ────────────────────────────────


def test_retention_sweeps_when_ttl_and_disk_cap_are_off(downloads):
    """The new default: a finished job expires even with TTL=0 and no ceiling."""
    old = make_job_dir(downloads, "old", age_hours=48)
    fresh = make_job_dir(downloads, "fresh", age_hours=0)

    assert jobs._sweep(ttl_hours=0, max_bytes=0) == 1
    assert not old.exists()
    assert fresh.exists()


def test_all_limits_zero_keeps_expired_downloads(downloads, retention_off):
    """Everything off — clocks included — means keep everything."""
    old = make_job_dir(downloads, "ancient", age_hours=1000)

    assert jobs._sweep(ttl_hours=0, max_bytes=0) == 0
    assert old.exists()


def test_undelivered_survives_long_clock_but_not_retention(downloads, monkeypatch):
    """A finished file no one fetched yet is kept for DOWNLOAD_RETENTION_HOURS."""
    monkeypatch.setattr(jobs, "DOWNLOAD_RETENTION_HOURS", 10)
    monkeypatch.setattr(jobs, "DELIVERED_RETENTION_HOURS", 1)
    young = make_job_dir(downloads, "young", age_hours=2)
    old = make_job_dir(downloads, "old", age_hours=11)

    assert jobs._sweep(ttl_hours=0, max_bytes=0) == 1
    assert young.exists()   # 2h < 10h retention
    assert not old.exists()  # past the undelivered clock


def test_delivered_job_goes_on_the_shorter_clock(downloads, monkeypatch):
    """Once the user fetched a file, the server copy may go much sooner."""
    monkeypatch.setattr(jobs, "DOWNLOAD_RETENTION_HOURS", 10)
    monkeypatch.setattr(jobs, "DELIVERED_RETENTION_HOURS", 1)
    delivered = make_job_dir(downloads, "delivered", age_hours=2)
    jobs._jobs["delivered"] = done_job("delivered", delivered=True)
    untouched = make_job_dir(downloads, "untouched", age_hours=2)
    jobs._jobs["untouched"] = done_job("untouched", delivered=False)

    assert jobs._sweep(ttl_hours=0, max_bytes=0) == 1
    assert not delivered.exists()  # 2h > 1h delivered clock
    assert untouched.exists()      # 2h < 10h undelivered clock


def test_intermediates_only_job_is_removed_immediately(downloads):
    """A settled job reduced to part files / sidecars never waits."""
    leftover = make_intermediate_job_dir(downloads, "halfdone")

    assert jobs._sweep(ttl_hours=0, max_bytes=0) == 1
    assert not leftover.exists()


def test_intermediates_of_a_finished_job_are_removed_immediately(
    downloads, monkeypatch
):
    """Even a fresh finished job is cleaned if it holds only intermediates."""
    monkeypatch.setattr(jobs, "DOWNLOAD_RETENTION_HOURS", 1000)
    leftover = make_intermediate_job_dir(downloads, "halfdone")
    jobs._jobs["halfdone"] = done_job("halfdone", delivered=False)

    assert jobs._sweep(ttl_hours=0, max_bytes=0) == 1
    assert not leftover.exists()


def test_finished_job_drops_intermediates_but_keeps_the_final_file(
    downloads, monkeypatch
):
    """A settled job's sidecars (torrent control dir, subs, parts) go the
    moment it finishes; the final media itself waits for the retention clock."""
    monkeypatch.setattr(jobs, "DOWNLOAD_RETENTION_HOURS", 100)
    path = downloads / "job1"
    path.mkdir()
    (path / "Episode 1.mp4").write_bytes(b"\0" * 100)
    # The Nyaa torrent work dir + a subtitle sidecar, both intermediates.
    (path / "Episode 1.nyaatmp").mkdir()
    (path / "Episode 1.nyaatmp" / "piece.data").write_bytes(b"\0" * 50)
    (path / "Episode 1.fas.srt").write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nHi\n")
    jobs._jobs["job1"] = done_job("job1", delivered=False)

    assert jobs._sweep(ttl_hours=0, max_bytes=0) == 0  # final file retained
    assert (path / "Episode 1.mp4").exists()
    assert not (path / "Episode 1.nyaatmp").exists()
    assert not (path / "Episode 1.fas.srt").exists()


def test_orphan_directory_expires_like_an_undelivered_job(downloads, monkeypatch):
    """A leftover folder from a previous run follows the undelivered clock."""
    monkeypatch.setattr(jobs, "DOWNLOAD_RETENTION_HOURS", 1)
    monkeypatch.setattr(jobs, "DELIVERED_RETENTION_HOURS", 0.25)
    orphan = make_job_dir(downloads, "orphan", age_hours=2)
    fresh = make_job_dir(downloads, "fresh", age_hours=0)

    assert jobs._sweep(ttl_hours=0, max_bytes=0) == 1
    assert not orphan.exists()
    assert fresh.exists()


def test_non_job_files_in_the_root_are_never_touched(downloads):
    """A user's own file next to the job folders is out of scope."""
    user_file = downloads / "my-own-music.mp3"
    user_file.write_bytes(b"\0" * 10)
    user_dir = downloads / "my-folder"
    user_dir.mkdir()

    assert jobs._sweep(ttl_hours=0, max_bytes=0) == 0
    assert user_file.exists()
    assert user_dir.exists()


# ── legacy TTL + disk ceiling ───────────────────────────────────────────────


def test_ttl_zero_with_a_disk_cap_still_enforces_the_cap(downloads, retention_off):
    """Turning the clock off must not turn the disk budget off with it."""
    old = make_job_dir(downloads, "old", age_hours=100, size=800)
    new = make_job_dir(downloads, "new", age_hours=1, size=800)

    assert jobs._sweep(ttl_hours=0, max_bytes=1000) == 1
    assert not old.exists()  # oldest first
    assert new.exists()


def test_disk_cap_zero_keeps_everything_over_budget(downloads, retention_off):
    big = make_job_dir(downloads, "big", age_hours=1, size=10_000)

    assert jobs._sweep(ttl_hours=24, max_bytes=0) == 0
    assert big.exists()


def test_ttl_still_expires_when_only_the_disk_cap_is_off(downloads, retention_off):
    old = make_job_dir(downloads, "old", age_hours=48)
    fresh = make_job_dir(downloads, "fresh", age_hours=1)

    assert jobs._sweep(ttl_hours=24, max_bytes=0) == 1
    assert not old.exists()
    assert fresh.exists()


def test_all_limits_off_never_walks_the_directory(downloads, monkeypatch, retention_off):
    """A library of thousands of files is not stat()ed every ten minutes to
    decide, each time, that nothing may be deleted."""
    make_job_dir(downloads, "kept", age_hours=1000)
    monkeypatch.setattr(
        jobs, "_measure", lambda path: pytest.fail("walked with all limits off")
    )

    assert jobs._sweep(ttl_hours=0, max_bytes=0) == 0


def test_defaults_still_expire_and_cap(downloads):
    """The public-server behaviour the defaults describe is unchanged."""
    old = make_job_dir(downloads, "old", age_hours=jobs.DOWNLOADS_TTL_HOURS + 1)
    fresh = make_job_dir(downloads, "fresh", age_hours=0)

    assert jobs._sweep() == 1
    assert not old.exists()
    assert fresh.exists()


def test_a_running_job_is_never_swept(downloads):
    path = make_job_dir(downloads, "running", age_hours=1000)
    job = jobs.Job(id="running", name="in flight")
    job.tracks["t1"] = jobs.TrackState(track=None, filename="t1", status="downloading")
    jobs._jobs["running"] = job

    assert jobs._sweep(ttl_hours=1, max_bytes=1) == 0
    assert path.exists()


# ── low-disk guard on start() ───────────────────────────────────────────────


def test_start_refuses_when_disk_is_low(downloads, monkeypatch):
    monkeypatch.setattr(jobs, "MIN_FREE_DISK_MB", 2048)

    class _Usage:
        free = 1024 * 1024**2  # 1 GiB — under the 2 GiB floor

    monkeypatch.setattr(jobs.shutil, "disk_usage", lambda _p: _Usage())
    track = Track(id="t1", title="T", artists=["A"], album="Al",
                  duration_ms=1, cover_url=None)

    with pytest.raises(jobs.DiskFullError):
        jobs.start("album", [track], quality="128")


def test_check_disk_passes_with_room(downloads, monkeypatch):
    monkeypatch.setattr(jobs, "MIN_FREE_DISK_MB", 2048)

    class _Usage:
        free = 50 * 1024**3  # 50 GiB

    monkeypatch.setattr(jobs.shutil, "disk_usage", lambda _p: _Usage())

    jobs._check_disk()  # must not raise


def test_check_disk_off_when_zero(downloads, monkeypatch):
    monkeypatch.setattr(jobs, "MIN_FREE_DISK_MB", 0)
    monkeypatch.setattr(
        jobs.shutil, "disk_usage", lambda _p: pytest.fail("checked disk with the guard off")
    )

    jobs._check_disk()
