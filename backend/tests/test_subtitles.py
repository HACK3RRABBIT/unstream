"""Subtitle parsing, normalization, translation and muxing — hermetic, no network.

The translation provider's HTTP boundary is replaced with a fake translator; the
SQLite cache is pointed at a temp file; ffmpeg is mocked for the muxer's args.
"""

import pytest

from app.anime import subtitle_translate
from app.anime.subtitles import build_srt, mux_subtitles, normalize_srt, parse_srt


# ── parsing / normalization ──────────────────────────────────────────────────


def test_srt_parse_build_roundtrip_preserves_timestamps():
    srt = (
        "1\n00:00:01,000 --> 00:00:04,000\nHello world\n\n"
        "2\n00:00:05,500 --> 00:00:08,250\nSecond line\nwith more\n"
    )
    cues = parse_srt(srt)
    assert [c.index for c in cues] == [1, 2]
    assert cues[0].start == "00:00:01,000"
    assert cues[0].end == "00:00:04,000"
    assert cues[0].text == "Hello world"
    assert cues[1].text == "Second line\nwith more"
    assert build_srt(cues) == srt  # byte-for-byte round-trip


def test_vtt_normalizes_to_srt():
    vtt = (
        b"WEBVTT\n"
        b"\n"
        b"00:00:01.000 --> 00:00:04.000 align:start position:10%\n"
        b"Hello\n"
        b"\n"
        b"NOTE a comment\n"
        b"\n"
        b"cue-id-2\n"
        b"00:00:05.500 --> 00:00:08.250\n"
        b"Second line\n"
    )
    assert normalize_srt(vtt) == (
        "1\n00:00:01,000 --> 00:00:04,000\nHello\n\n"
        "2\n00:00:05,500 --> 00:00:08,250\nSecond line\n"
    )


def test_srt_passthrough_normalize_unchanged():
    raw = b"1\n00:00:01,000 --> 00:00:04,000\nHello\n"
    assert normalize_srt(raw) == "1\n00:00:01,000 --> 00:00:04,000\nHello\n"


# ── translation ──────────────────────────────────────────────────────────────


class _FakeTranslator(subtitle_translate.Translator):
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def translate_text(self, text: str, target: str) -> str:
        self.calls.append((text, target))
        return f"ترجمه: {text}"


def test_translate_dialogue_only_preserves_timestamps():
    translator = _FakeTranslator()
    srt = (
        "1\n00:00:01,000 --> 00:00:04,000\nHello world\n\n"
        "2\n00:00:05,500 --> 00:00:08,250\n\n"  # empty dialogue stays empty
    )
    out = subtitle_translate.translate_dialogue(srt, "fa", translator)
    assert "00:00:01,000 --> 00:00:04,000" in out
    assert "00:00:05,500 --> 00:00:08,250" in out
    assert "ترجمه: Hello world" in out
    assert translator.calls == [("Hello world", "fa")]  # only dialogue, not timestamps


def _fresh_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(subtitle_translate, "_DB_PATH", tmp_path / "subs.db")
    monkeypatch.setattr(subtitle_translate, "_conn", None)


def test_translate_srt_file_caches_by_content_hash(monkeypatch, tmp_path):
    _fresh_cache(tmp_path, monkeypatch)
    translator = _FakeTranslator()
    src = tmp_path / "en.srt"
    src.write_text("1\n00:00:01,000 --> 00:00:04,000\nHello world\n")

    dest1 = tmp_path / "fa1.srt"
    assert subtitle_translate.translate_srt_file(src, "fa", dest1, translator) is dest1
    assert dest1.exists()
    assert "ترجمه: Hello world" in dest1.read_text()
    assert len(translator.calls) == 1

    # Same English subtitle -> cache hit, no second translation.
    dest2 = tmp_path / "fa2.srt"
    assert subtitle_translate.translate_srt_file(src, "fa", dest2, translator) is dest2
    assert translator.calls == [("Hello world", "fa")]

    # Changed English subtitle -> new content hash -> translated again.
    src.write_text("1\n00:00:01,000 --> 00:00:04,000\nGoodbye\n")
    dest3 = tmp_path / "fa3.srt"
    assert subtitle_translate.translate_srt_file(src, "fa", dest3, translator) is dest3
    assert translator.calls == [("Hello world", "fa"), ("Goodbye", "fa")]


def test_translate_srt_file_failure_returns_none(monkeypatch, tmp_path):
    _fresh_cache(tmp_path, monkeypatch)
    src = tmp_path / "en.srt"
    src.write_text("1\n00:00:01,000 --> 00:00:04,000\nHello\n")

    class _Boom(subtitle_translate.Translator):
        def translate_text(self, text, target):
            raise RuntimeError("rate limited")

    assert subtitle_translate.translate_srt_file(src, "fa", tmp_path / "fa.srt", _Boom()) is None
    assert not (tmp_path / "fa.srt").exists()


# ── muxing ───────────────────────────────────────────────────────────────────


def test_mux_subtitles_empty_returns_video(tmp_path):
    video = tmp_path / "ep.mp4"
    video.write_bytes(b"v")
    assert mux_subtitles(video, [], tmp_path / "dest") is video


def test_mux_subtitles_multi_track_args(monkeypatch, tmp_path):
    from app import downloader
    from app.anime import downloader as anime_downloader

    video = tmp_path / "ep.mp4"
    video.write_bytes(b"v")
    sub1 = tmp_path / "en.srt"
    sub1.write_text("1\n00:00:01,000 --> 00:00:04,000\nHello\n")
    sub2 = tmp_path / "fa.srt"
    sub2.write_text("1\n00:00:01,000 --> 00:00:04,000\nسلام\n")

    captured: dict = {}

    def fake_ffmpeg(args, produced, what):
        captured["args"] = args
        produced.write_bytes(b"muxed")

    monkeypatch.setattr(downloader, "_run_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(anime_downloader, "_video_has_audio", lambda v: True)

    out = mux_subtitles(video, [("eng", sub1), ("fas", sub2)], tmp_path / "dest")
    args = captured["args"]
    assert args.count("mov_text") == 2
    assert "1:0" in args and "2:0" in args
    assert "language=eng" in args and "language=fas" in args
    assert out == tmp_path / "dest.mp4"
    assert out.read_bytes() == b"muxed"
