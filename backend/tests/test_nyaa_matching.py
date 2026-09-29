"""Nyaa release-title matching against real titles.

Every title here was copied from a live nyaa.si search. Each case is one the
old matcher got wrong: a Season 2 episode served for a Season 1 request, an
unrelated show matched through its CRC hash, a whole-season pack treated as a
single episode, a dub preferred over the subbed release.
"""

import pytest

from app.anime import nyaa
from app.anime.providers import EpisodeSource

Provider = nyaa.NyaaProvider


def _row(torrent_id: int, title: str, seeders: int) -> str:
    title = title.replace("'", "&#39;")
    return (
        "<tr><td><a title=\"Anime - English-translated\"></a></td>"
        f'<td colspan="2"><a href="/view/{torrent_id}" title="{title}"></a></td>'
        f'<td><a href="/download/{torrent_id}.torrent"></a>'
        f'<a href="magnet:?xt=urn:btih:{torrent_id:040d}"></a></td>'
        '<td class="text-center">1.2 GiB</td>'
        f'<td class="text-center">{seeders}</td></tr>'
    )


def _page(*rows: str) -> str:
    return (
        '<table class="table torrent-list"><thead><tr><th>h</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _parse(page: str, episode: int, season: int, titles: list[str]):
    singles, batches = Provider._parse_rows(page, episode, season, titles)
    return [t["title"] for t in singles], [t["title"] for t in batches]


FRIEREN = [
    "[SubsPlease] Sousou no Frieren S2 - 03 (1080p) [7556A22B].mkv",
    "[Erai-raws] Sousou no Frieren 2nd Season - 03 [1080p CR WEB-DL AVC AAC][MultiSub][86271E51]",
    "[ToonsHub] Frieren Beyond Journeys End S02E03 1080p BILI WEB-DL AAC2.0 H.265 (Sousou no Frieren, Multi-Subs)",
    "[SubsPlease] Sousou no Frieren - 03 (1080p) [7EF3F175].mkv",
    "[Judas] Sousou no Frieren (Frieren: Beyond Journey's End) - S01E03 [1080p][HEVC x265 10bit][Multi-Subs] (Weekly)",
    "[SubsPlease] Sousou no Frieren - 23 (1080p) [1310F3C5].mkv",
]


def test_season_one_request_skips_second_season_releases():
    page = _page(*(_row(i, t, 100) for i, t in enumerate(FRIEREN, 1)))
    singles, batches = _parse(page, 3, 1, ["Frieren: Beyond Journey's End", "Sousou no Frieren"])
    assert singles == [FRIEREN[3], FRIEREN[4]]
    assert batches == []


def test_season_two_request_takes_every_second_season_form():
    page = _page(*(_row(i, t, 100) for i, t in enumerate(FRIEREN, 1)))
    singles, _ = _parse(page, 3, 2, ["Sousou no Frieren 2nd Season"])
    # "S2 - 03" used to parse as the *range* 2..3, making it a batch.
    assert singles == FRIEREN[:3]


def test_crc_hash_is_not_an_episode_marker_and_other_shows_are_rejected():
    page = _page(
        _row(1, "[SubsPlease] Grow Up Show - Himawari no Circus-dan - 05 (1080p) [EDA405E2].mkv", 900),
        _row(2, "[SubsPlease] Dandadan - 02 (1080p) [5F956413].mkv", 50),
        _row(3, "[SubsPlease] Dandadan - 03 (720p) [02ECF06E].mkv", 50),
    )
    singles, _ = _parse(page, 2, 1, ["DAN DA DAN", "Dandadan"])
    assert singles == ["[SubsPlease] Dandadan - 02 (1080p) [5F956413].mkv"]


def test_words_run_together_still_match_the_show():
    assert nyaa._title_matches("[SubsPlease] Dandadan - 02 (1080p)", ["DAN DA DAN"])
    assert nyaa._title_matches(
        "[ToonsHub] Frieren Beyond Journeys End S02E03", ["Frieren: Beyond Journey's End"]
    )
    assert nyaa._title_matches("[Commie] Fate ⁄ Zero - 02 [F1693F31].mkv", ["Fate/Zero"])
    assert not nyaa._title_matches("[X] Himawari no Circus-dan - 05", ["DAN DA DAN"])


def test_season_pack_holds_the_episode_but_other_seasons_do_not():
    page = _page(
        _row(1, "Fate/Zero (2011) S01 [1080p x265 HEVC 10bit BluRay Dual Audio AAC] [Prof]", 37),
        _row(2, "Fate/Zero.S02.1080p.Blu-Ray.10-Bit.Dual-Audio.LPCM.x265-iAHD", 22),
        _row(3, "[phazer11] Fate/Zero 2nd Season | Fate/Zero Season 2 [Dual Audio 10bit BD1080p][HEVC-x265]", 8),
        _row(4, "[HorribleSubs] Fate Zero (01-25) [1080p] (Batch)", 18),
    )
    singles, batches = _parse(page, 2, 1, ["Fate/Zero"])
    # The Season 2 pack used to be a *single* for episode 2 ("Season 2 [").
    assert singles == []
    assert batches == [
        "Fate/Zero (2011) S01 [1080p x265 HEVC 10bit BluRay Dual Audio AAC] [Prof]",
        "[HorribleSubs] Fate Zero (01-25) [1080p] (Batch)",
    ]


def test_codec_and_resolution_numbers_do_not_make_a_range():
    page = _page(
        _row(1, "One Piece - 1100 - 1080p WEB x264 -NanDesuKa (CR).mkv", 30),
        _row(2, "[RedDeadProject] One Piece - 1100 & 1101 - [JPBD] [HEVC] [x265]", 30),
    )
    singles, batches = _parse(page, 1100, 1, ["ONE PIECE"])
    assert singles == ["One Piece - 1100 - 1080p WEB x264 -NanDesuKa (CR).mkv"]
    assert batches == ["[RedDeadProject] One Piece - 1100 & 1101 - [JPBD] [HEVC] [x265]"]


def test_a_number_in_the_show_name_is_not_the_episode():
    page = _page(
        _row(1, "[SubsPlease] Kaijuu 8-gou - 03 (1080p) [AAAAAAAA].mkv", 40),
        _row(2, "[SubsPlease] Kaijuu 8-gou - 05 (1080p) [BBBBBBBB].mkv", 90),
    )
    singles, _ = _parse(page, 8, 1, ["Kaiju No. 8", "Kaijuu 8-gou"])
    assert singles == []


def test_version_suffix_still_names_the_episode():
    page = _page(_row(1, "[Reported] Fate Zero 02 v3.mkv", 3), _row(2, "[X] Fate Zero - 02v2 [720p]", 3))
    singles, _ = _parse(page, 2, 1, ["Fate/Zero"])
    assert len(singles) == 2


def test_dub_only_wins_when_nothing_else_is_seeded():
    subbed = {"torrent_id": "1", "title": "sub", "seeders": 9, "explicit": False, "dub": False}
    dub = {"torrent_id": "2", "title": "dub", "seeders": 14, "explicit": True, "dub": True}
    assert nyaa._best_seeded({"1": subbed, "2": dub})["title"] == "sub"
    assert nyaa._best_seeded({"2": dub})["title"] == "dub"


def test_a_dead_row_never_outranks_a_live_one():
    dead = {"torrent_id": "1", "title": "dead", "seeders": 0, "explicit": True}
    live = {"torrent_id": "2", "title": "live", "seeders": 1, "explicit": False}
    assert nyaa._best_seeded({"1": dead, "2": live})["title"] == "live"


def test_queries_use_romaji_padding_and_the_title_declared_season():
    src = EpisodeSource(
        provider="nyaa", anime_id="Mushoku Tensei: Jobless Reincarnation Season 2",
        anime_title="Mushoku Tensei: Jobless Reincarnation Season 2", year=None,
        season=3, episode=1, alt_titles=("Mushoku Tensei II: Isekai Ittara Honki Dasu",),
    )
    queries, season, _titles = Provider._queries(src, 1)
    # The franchise counts the split cour as season 2, so this entry is its
    # third — but releases call it Season 2, and the title says so.
    assert season == 2
    assert queries == [
        "Mushoku Tensei: Jobless Reincarnation S02E01",
        "Mushoku Tensei: Jobless Reincarnation Season 2 01",
        "Mushoku Tensei II: Isekai Ittara Honki Dasu S02E01",
        "Mushoku Tensei II: Isekai Ittara Honki Dasu 01",
    ]


def test_weak_single_loses_to_a_healthy_batch(monkeypatch):
    pages = {
        "Fate/Zero S01E02": _page(),
        "Fate/Zero 02": _page(_row(1, "[Commie] Fate ⁄ Zero - 02 [F1693F31].mkv", 1)),
        "Fate/Zero": _page(_row(2, "Fate/Zero (2011) S01 [1080p BluRay Dual Audio AAC] [Prof]", 37)),
    }

    class Resp:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        nyaa._client, "get", lambda url, params=None, **kw: Resp(pages[params["q"]])
    )
    src = EpisodeSource("nyaa", "Fate/Zero", "Fate/Zero", None, 1, 2)
    torrent = Provider()._search_episode(src, 2, "original")
    assert torrent["torrent_id"] == "2"
    assert torrent["batch"] is True


@pytest.mark.parametrize(
    "path, episode, season, expected",
    [
        ("Fate Zero/[HorribleSubs] Fate Zero - 02 [1080p].mkv", 2, 1, True),
        ("Fate Zero/[HorribleSubs] Fate Zero - 12 [1080p].mkv", 2, 1, False),
        ("Show/[Group] Show - 05 [ABCD1E02].mkv", 2, 1, False),  # CRC, not E02
        ("Show S01-S02/Season 2/Show S02E02.mkv", 2, 1, False),
        ("Show S01-S02/Season 1/Show S01E02.mkv", 2, 1, True),
        ("Show/Show - 01-02.mkv", 2, 1, False),  # a double episode
    ],
)
def test_batch_file_selection(path, episode, season, expected):
    assert Provider._file_is_episode(path, episode, season) is expected
