"""What "original" quality promises and delivers.

An original download keeps the upload's own audio without re-encoding, but it
must arrive as a pure audio file: a video's combined mp4 has the song's audio
inside it, and shipping a music video where a song was promised is the kind of
"m4a with bad quality" this pipeline is meant to avoid. _keep_original is the
guarantee — a copy-codec remux that drops the picture, never re-encodes.
"""

import shutil
import subprocess

import pytest

from app import downloader

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _ffmpeg(args: list[str], out: str) -> None:
    subprocess.run(
        [FFMPEG, "-y", *args, out], check=True, capture_output=True, timeout=120
    )


def _streams(path, kind: str) -> list[str]:
    result = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            kind,
            "-show_entries",
            "stream=codec_name",
            "-of",
            "csv=p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return [line for line in result.stdout.splitlines() if line]


@pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="ffmpeg/ffprobe required")
def test_combined_mp4_is_remuxed_to_pure_m4a(tmp_path):
    dest = tmp_path / "song"
    _ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=32x32:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
        ],
        str(dest) + ".mp4",
    )

    out = downloader._keep_original(dest)

    assert out == tmp_path / "song.m4a"
    assert not dest.with_name("song.mp4").exists()  # the video file is gone
    assert _streams(out, "v") == []  # no picture track
    assert len(_streams(out, "a")) == 1


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")
def test_a_real_m4a_passes_through_untouched(tmp_path):
    dest = tmp_path / "song"
    _ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac"], str(dest) + ".m4a")

    out = downloader._keep_original(dest)

    assert out.name == "song.m4a"
    assert dest.with_name("song.m4a").exists()
    assert len(_streams(out, "a")) == 1
