"""Resolution as a rung: what the pipeline calls a frame, and what it refuses.

The bug these cover: every quality gate compared pixel heights for equality,
so a real 1080p release cut at 1920x804 (any widescreen film), a 4:3 remaster
at 1440x1080, or an encoder's 1072 were all "not the quality you asked for" —
the download succeeded and was then thrown away, and the picker reported the
resolution as unavailable. Rungs fix that without loosening the guarantee that
a 720p file is never passed off as the 1080p that was requested.
"""

from app.anime import quality
from app.anime.downloader import _check_served_quality
from app.anime.providers import QualityUnavailable

import pytest


class TestRung:
    def test_standard_frames_keep_their_name(self):
        assert quality.rung(1920, 1080) == 1080
        assert quality.rung(1280, 720) == 720
        assert quality.rung(854, 480) == 480
        assert quality.rung(640, 360) == 360

    def test_widescreen_cut_is_named_by_its_width(self):
        """2.39:1 at 1920 wide is the 1080p release, not an "804p" one."""
        assert quality.rung(1920, 804) == 1080
        assert quality.rung(1280, 536) == 720
        assert quality.rung(1920, 800) == 1080

    def test_four_three_is_named_by_its_height(self):
        """A 4:3 remaster is 1440x1080 — narrower, still 1080p."""
        assert quality.rung(1440, 1080) == 1080
        assert quality.rung(640, 480) == 480

    def test_encoder_rounding_snaps_to_the_rung(self):
        assert quality.rung(1912, 1072) == 1080
        assert quality.rung(1918, 1078) == 1080
        assert quality.rung(848, 476) == 480

    def test_off_ladder_frame_keeps_its_own_number(self):
        """900p is not 1080p and not 720p — it answers only to 900."""
        assert quality.rung(1600, 900) == 900
        assert not quality.matches("1080", 1600, 900)
        assert not quality.matches("720", 1600, 900)
        assert quality.matches("900", 1600, 900)

    def test_height_only_still_classifies(self):
        """Width is often unknown (the height-only ffprobe fallback)."""
        assert quality.rung(None, 1080) == 1080
        assert quality.rung(None, 720) == 720

    def test_no_height_is_no_verdict(self):
        assert quality.rung(1920, None) is None
        assert quality.rung(None, None) is None


class TestMatches:
    def test_original_matches_anything(self):
        assert quality.matches("original", 1920, 1080)
        assert quality.matches("original", 640, 360)

    def test_neighbouring_rungs_never_match(self):
        assert not quality.matches("1080", 1280, 720)
        assert not quality.matches("720", 1920, 1080)
        assert not quality.matches("480", 1280, 720)

    def test_unprobed_frame_is_not_a_match(self):
        assert not quality.matches("1080", None, None)


class TestServedQualityCheck:
    def test_widescreen_1080p_download_is_accepted(self):
        """The regression: this used to raise and throw away a good file."""
        _check_served_quality("1080", 804, 1920)

    def test_four_three_1080p_download_is_accepted(self):
        _check_served_quality("1080", 1080, 1440)

    def test_wrong_rung_is_still_refused(self):
        with pytest.raises(QualityUnavailable):
            _check_served_quality("1080", 720, 1280)

    def test_unverifiable_file_is_still_refused(self):
        with pytest.raises(QualityUnavailable):
            _check_served_quality("1080", None, None)

    def test_original_is_never_checked(self):
        _check_served_quality("original", 396, 704)

    def test_height_only_probe_still_enforces(self):
        """Without a width, the height alone decides — the old behaviour."""
        _check_served_quality("720", 720, None)
        with pytest.raises(QualityUnavailable):
            _check_served_quality("720", 480, None)
