"""AniList provider and anime routes — no network ever touched.

Follows the same discipline as test_lyrics.py: the module's HTTP boundary
(`anilist._gql`) is monkeypatched, so these tests assert on mapping,
franchise grouping and route shapes against canned AniList JSON.
"""

import pytest

from app.anime import anilist
from app.models import ProviderError


def node(
    media_id,
    romaji="",
    english=None,
    fmt="TV",
    episodes=0,
    year=None,
    relations=None,
):
    """A fake AniList Page.media node."""
    return {
        "id": media_id,
        "format": fmt,
        "episodes": episodes,
        "seasonYear": year,
        "status": "FINISHED",
        "coverImage": {"large": f"https://img/{media_id}.jpg"},
        "description": None,
        "title": {"romaji": romaji, "english": english, "native": None},
        "synonyms": [],
        "relations": {
            "edges": [
                {"relationType": rtype, "node": {"id": rid, "format": "TV"}}
                for rtype, rid in (relations or [])
            ]
        },
    }


def test_search_maps_fields(monkeypatch):
    monkeypatch.setattr(
        anilist,
        "_gql",
        lambda variables: {
            "media": [
                node(20, romaji="NARUTO", english="Naruto", episodes=220, year=2002)
            ]
        },
    )
    results = anilist.search("naruto")
    assert len(results) == 1
    media = results[0]
    assert media.id == 20
    assert media.best_title == "Naruto"  # english wins over romaji
    assert media.episodes == 220
    assert media.season_year == 2002
    assert media.cover_url == "https://img/20.jpg"


def test_search_sends_anime_and_excludes_adult(monkeypatch):
    captured = {}

    def fake_gql(variables):
        captured.update(variables)
        return {"media": []}

    monkeypatch.setattr(anilist, "_gql", fake_gql)
    anilist.search("anything")
    assert captured["type"] == "ANIME"
    assert captured["isAdult"] is False


def test_franchise_walks_sequels_and_sorts_by_year(monkeypatch):
    # 1 -> sequel 2 -> sequel 3; plus a spin-off (not a sequel) and a movie.
    by_id = {
        1: node(1, romaji="Alpha", episodes=12, year=2000,
                relations=[("SEQUEL", 2), ("SPIN_OFF", 9)]),
        2: node(2, romaji="Alpha II", episodes=12, year=2003,
                relations=[("SEQUEL", 3)]),
        3: node(3, romaji="Alpha III", episodes=10, year=2006,
                relations=[]),
        9: node(9, romaji="Alpha Chibi", episodes=1, year=2004),
    }

    def fake_get(media_id):
        return anilist._media_from_node(by_id[media_id])

    def fake_gql(variables):
        mid = variables["id"]
        if mid not in by_id:
            return {"media": []}
        return {"media": [by_id[mid]]}

    monkeypatch.setattr(anilist, "get", fake_get)
    monkeypatch.setattr(anilist, "_gql", fake_gql)
    monkeypatch.setattr(anilist, "_franchise_cache", {})

    seasons = anilist.franchise(1)
    # Spin-off (id 9) is reachable but not via SEQUEL/PREQUEL, so dropped.
    assert [m.id for m in seasons] == [1, 2, 3]


def test_franchise_unreleased_sorts_last(monkeypatch):
    by_id = {
        1: node(1, romaji="Alpha", episodes=12, year=2000, relations=[("SEQUEL", 2)]),
        2: node(2, romaji="Alpha Next", episodes=0, year=None, relations=[]),
    }

    def fake_get(media_id):
        return anilist._media_from_node(by_id[media_id])

    def fake_gql(variables):
        return {"media": [by_id[variables["id"]]]}

    monkeypatch.setattr(anilist, "get", fake_get)
    monkeypatch.setattr(anilist, "_gql", fake_gql)
    monkeypatch.setattr(anilist, "_franchise_cache", {})

    seasons = anilist.franchise(1)
    assert [m.id for m in seasons] == [1, 2]


def test_franchise_caches(monkeypatch):
    calls = {"n": 0}
    by_id = {1: node(1, romaji="Alpha", relations=[])}

    def fake_get(media_id):
        calls["n"] += 1
        return anilist._media_from_node(by_id[media_id])

    def fake_gql(variables):
        return {"media": [by_id[variables["id"]]]}

    monkeypatch.setattr(anilist, "get", fake_get)
    monkeypatch.setattr(anilist, "_gql", fake_gql)
    monkeypatch.setattr(anilist, "_franchise_cache", {})

    anilist.franchise(1)
    anilist.franchise(1)
    assert calls["n"] == 1  # second call served from cache


def test_dangling_relation_does_not_break_franchise(monkeypatch):
    # A SEQUEL edge pointing at an id that no longer exists on AniList.
    by_id = {1: node(1, romaji="Alpha", relations=[("SEQUEL", 999)])}

    def fake_get(media_id):
        if media_id not in by_id:
            raise ProviderError("not found")
        return anilist._media_from_node(by_id[media_id])

    def fake_gql(variables):
        return {"media": [] if variables["id"] not in by_id else [by_id[variables["id"]]]}

    monkeypatch.setattr(anilist, "get", fake_get)
    monkeypatch.setattr(anilist, "_gql", fake_gql)
    monkeypatch.setattr(anilist, "_franchise_cache", {})

    assert [m.id for m in anilist.franchise(1)] == [1]


def test_get_raises_provider_error_when_missing(monkeypatch):
    monkeypatch.setattr(anilist, "_gql", lambda variables: {"media": []})
    with pytest.raises(ProviderError):
        anilist.get(12345)


def test_routes_search_shape(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    naruto = anilist._media_from_node(node(20, romaji="NARUTO", episodes=220))
    monkeypatch.setattr(anilist, "search", lambda query, limit=12: [naruto])
    # The search route walks each TV result's franchise to size season_count;
    # stub that live AniList call with the result's own single-season chain.
    monkeypatch.setattr(anilist, "franchise", lambda media_id: [naruto])
    client = TestClient(app)
    resp = client.get("/api/anime/search?q=naruto")
    assert resp.status_code == 200
    body = resp.json()
    # A single TV result lands in `series` (not `movies`), with season_count.
    assert len(body["series"]) == 1
    assert body["movies"] == []
    series = body["series"][0]
    assert series["title"] == "NARUTO"
    assert series["episodes"] == 220
    assert series["season_count"] >= 1


def test_routes_search_splits_movies_from_series(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    naruto = anilist._media_from_node(node(20, romaji="NARUTO", episodes=220))
    movie = anilist._media_from_node(
        node(936, romaji="NARUTO Movie", fmt="MOVIE", episodes=1)
    )

    monkeypatch.setattr(anilist, "search", lambda query, limit=12: [naruto, movie])
    # Only the TV result triggers a franchise walk; stub that live AniList call
    # with naruto's own single-season chain.
    monkeypatch.setattr(anilist, "franchise", lambda media_id: [naruto])
    client = TestClient(app)
    resp = client.get("/api/anime/search?q=naruto")
    body = resp.json()
    assert len(body["series"]) == 1
    assert len(body["movies"]) == 1
    assert body["movies"][0]["format"] == "MOVIE"


def test_routes_empty_query_400(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.get("/api/anime/search?q=%20%20")
    assert resp.status_code == 400


def test_airing_show_reports_available_episodes():
    """A RELEASING show lists its planned total but only the aired count as
    available (next-airing episode minus one)."""
    # node() doesn't set nextAiringEpisode; build one manually.
    node = anilist._media_from_node(
        {
            "id": 196187,
            "format": "TV",
            "episodes": 12,
            "seasonYear": 2025,
            "status": "RELEASING",
            "coverImage": {"large": None},
            "description": None,
            "title": {"romaji": "Super no Ura", "english": "Smoking Behind the Supermarket", "native": None},
            "synonyms": [],
            "nextAiringEpisode": {"episode": 7},  # 6 aired, ep 7 next
            "relations": {"edges": []},
        }
    )
    assert node.episodes == 12  # planned total
    assert node.status == "RELEASING"
    assert node.available_episodes == 6  # next(7) - 1


def test_finished_show_available_equals_planned():
    media = anilist._media_from_node(
        {
            "id": 1,
            "format": "TV",
            "episodes": 24,
            "seasonYear": 2020,
            "status": "FINISHED",
            "coverImage": {"large": None},
            "description": None,
            "title": {"romaji": "X", "english": None, "native": None},
            "synonyms": [],
            "relations": {"edges": []},
        }
    )
    assert media.available_episodes == 24


def test_description_html_is_stripped():
    """AniList descriptions are HTML; the API must hand back plain text."""
    node = {
        "id": 1,
        "format": "TV",
        "episodes": 24,
        "seasonYear": 2020,
        "status": "FINISHED",
        "coverImage": {"large": None},
        "description": "<i>Fancy</i> story.<br><b>Bold</b> &amp; <a href='x'>link</a>.",
        "title": {"romaji": "X", "english": None, "native": None},
        "synonyms": [],
        "relations": {"edges": []},
    }
    media = anilist._media_from_node(node)
    assert "<" not in media.description
    assert "Fancy" in media.description
    assert "&amp;" not in media.description  # entity unescaped
    assert "link" in media.description
