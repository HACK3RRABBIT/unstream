"""Spotify playlist top-up: rows past the embed page's 100-row cap.

The embed page renders at most 100 playlist rows, so _extend_playlist fetches
the tail from pathfinder, the GraphQL endpoint the web player itself pages
with. These tests pin that the tail is appended in order, that short playlists
never touch the network, and that a refusal — including a rotated persisted
query hash — degrades to the first 100 rows instead of an error.
"""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from app import embed
from app.models import Track


class FakeResp:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def tracks(n: int) -> list[Track]:
    return [
        Track(
            id=f"t{i}",
            title=f"Track {i}",
            artists=["Someone"],
            album="",
            duration_ms=0,
            cover_url=None,
        )
        for i in range(n)
    ]


def row(track_id: str, **overrides) -> dict:
    """One pathfinder playlist row, in the shape the real endpoint returns."""
    data = {
        "uri": f"spotify:track:{track_id}",
        "name": "Song",
        "artists": {"items": [{"profile": {"name": "Someone"}}]},
        "trackDuration": {"totalMilliseconds": 120_000},
        "albumOfTrack": {
            "name": "The Album",
            "coverArt": {
                "sources": [
                    {"url": "http://small.jpg", "width": 64},
                    {"url": "http://big.jpg", "width": 640},
                ]
            },
            "date": {"isoString": "1999-05-05T00:00:00Z"},
        },
    }
    data.update(overrides)
    return {"itemV2": {"__typename": "TrackResponseWrapper", "data": data}}


def test_short_playlist_never_touches_the_api(monkeypatch):
    monkeypatch.setattr(
        embed, "_anonymous_token", lambda: pytest.fail("no token needed")
    )
    monkeypatch.setattr(
        embed, "_api_playlist_tracks", lambda *a: pytest.fail("no api needed")
    )

    out = embed._extend_playlist(tracks(99), "abc")
    assert len(out) == 99


def test_playlist_at_cap_is_extended_in_order(monkeypatch):
    monkeypatch.setattr(embed, "_anonymous_token", lambda: "tok")

    def fake_tracks(spotify_id: str, token: str) -> list[dict]:
        assert (spotify_id, token) == ("abc", "tok")
        return [row(f"x{i}", name=f"Song {i}") for i in range(50)]

    monkeypatch.setattr(embed, "_api_playlist_tracks", fake_tracks)

    out = embed._extend_playlist(tracks(100), "abc")
    assert len(out) == 150
    tail = out[100]
    assert tail.id == "x0"  # the uri's id, not the uri
    assert tail.title == "Song 0"
    assert tail.track_number == 101  # rows continue past the embed's last one
    assert tail.artists == ["Someone"]
    assert tail.album == "The Album"
    assert tail.cover_url == "http://big.jpg"  # largest image, not the first
    assert tail.release_date == "1999"


def test_api_failure_keeps_the_embed_rows(monkeypatch):
    monkeypatch.setattr(
        embed,
        "_anonymous_token",
        lambda: (_ for _ in ()).throw(embed.ProviderError("no token")),
    )

    out = embed._extend_playlist(tracks(100), "abc")
    assert len(out) == 100


def test_undownloadable_rows_are_skipped(monkeypatch):
    monkeypatch.setattr(embed, "_anonymous_token", lambda: "tok")
    monkeypatch.setattr(
        embed,
        "_api_playlist_tracks",
        lambda *a: [
            {"itemV2": {"__typename": "EpisodeResponseWrapper", "data": {}}},
            {"itemV2": {"__typename": "UnknownType"}},
            row("ok1"),
            row("ok2"),
        ],
    )

    out = embed._extend_playlist(tracks(100), "abc")
    assert [t.id for t in out[100:]] == ["ok1", "ok2"]
    # Skipped rows must not leave gaps in the numbering.
    assert [t.track_number for t in out[100:]] == [101, 102]


def test_rotated_query_hash_is_a_provider_error(monkeypatch):
    """Pathfinder reports a rejected persisted query as 200 + errors."""
    body = json.dumps({"errors": [{"message": "PersistedQueryNotFound"}]}).encode()
    monkeypatch.setattr(embed, "urlopen", lambda req, **kw: FakeResp(body))

    with pytest.raises(embed.ProviderError):
        embed._playlist_page("abc", "tok", 100)

    # and _extend_playlist swallows it rather than failing the whole resolve
    monkeypatch.setattr(embed, "_anonymous_token", lambda: "tok")
    assert len(embed._extend_playlist(tracks(100), "abc")) == 100


def test_anonymous_token_uses_the_first_working_endpoint(monkeypatch):
    seen: list[str] = []

    def fake_open(req, **kwargs):
        seen.append(req.full_url)
        if "embed/api/token" in req.full_url:
            body = json.dumps({"accessToken": "embed-token"}).encode()
        else:
            # the classic endpoint gets skipped whenever the first answered
            body = b"not json at all"
        return FakeResp(body)

    monkeypatch.setattr(embed, "urlopen", fake_open)
    assert embed._anonymous_token() == "embed-token"
    assert len(seen) == 1


def test_anonymous_token_falls_back_across_endpoints(monkeypatch):
    def fake_open(req, **kwargs):
        return FakeResp(b"<html>blocked</html>")  # both endpoints refuse

    monkeypatch.setattr(embed, "urlopen", fake_open)
    with pytest.raises(embed.ProviderError):
        embed._anonymous_token()


def test_api_pagination_walks_the_tail(monkeypatch):
    total = 250

    def fake_open(req, **kwargs):
        params = parse_qs(urlparse(req.full_url).query)
        variables = json.loads(params["variables"][0])
        assert variables["uri"] == "spotify:playlist:abc"
        offset = variables["offset"]
        remaining = max(0, total - offset)
        batch = [row(f"i{offset + pos}") for pos in range(min(embed._PAGE, remaining))]
        body = {"data": {"playlistV2": {"content": {"items": batch, "totalCount": total}}}}
        return FakeResp(json.dumps(body).encode())

    monkeypatch.setattr(embed, "urlopen", fake_open)
    items = embed._api_playlist_tracks("abc", "tok")
    assert len(items) == 150  # rows 101..250, nothing past the end
    assert items[-1]["itemV2"]["data"]["uri"] == "spotify:track:i249"
