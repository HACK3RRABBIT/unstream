"""AniList metadata — search, franchise and seasons. No key, no account.

AniList exposes its whole catalog at graphql.anilist.co without any
authentication, which makes it a perfect metadata source for the anime
section — the same "public and keyless" constraint every other provider in
this project obeys. Search returns each season as its own Media entry with
an episode count, and the `relations` field walks SEQUEL/PREQUEL chains, so
a franchise (Naruto -> Season 1/2/3...) can be grouped without any external
knowledge.

This module only supplies metadata. The actual episode video comes from a
separate provider (see providers.py); here we just tell the UI what exists
and how many episodes each season has.
"""

import json
import time
from dataclasses import dataclass, field
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..models import ProviderError

API = "https://graphql.anilist.co"
_TIMEOUT = 15

# AniList rate-limits around 90 requests per minute. A franchise walk is
# several GraphQL calls, so cache franchise results briefly to stay well
# under that when a page is revisited. `lru_cache` has no TTL, hence the
# small dict + monotonic-clock expiry.
_FRANCHISE_TTL_SECONDS = 600
_franchise_cache: dict[int, tuple[float, list["AniMedia"]]] = {}


@dataclass
class AniMedia:
    """One AniList Media entry — the metadata a season or standalone anime needs."""

    id: int
    title_romaji: str
    title_english: str | None = None
    title_native: str | None = None
    synonyms: list[str] = field(default_factory=list)
    format: str = "TV"
    episodes: int = 0
    season_year: int | None = None
    status: str = ""
    cover_url: str | None = None
    description: str | None = None
    # The number of the next episode to air, or None when not airing. For a
    # RELEASING show, episodes aired so far = next_airing_episode - 1 — the
    # user is watching it live, so the list must show only what exists.
    next_airing_episode: int | None = None
    # (relationType, mediaId, format) — format included so search can tell a
    # series' sequel from a movie without an extra fetch.
    relations: list[tuple[str, int, str]] = field(default_factory=list)

    @property
    def best_title(self) -> str:
        """The name to show: English, else romaji, else native.

        English is deliberately first — romaji is often a romanization of a
        long Japanese title that a reader can't parse ("Ore wa Subete wo
        [Parry] Suru: ..." vs "I Parry Everything"), and AniList almost always
        carries a proper English title for series a viewer would search for.
        """
        return self.title_english or self.title_romaji or self.title_native or ""

    @property
    def available_episodes(self) -> int:
        """How many episodes actually exist right now.

        A RELEASING show lists its *planned* total; the next-airing number is
        one past what has aired. A finished or unaired show just reports the
        total (0 when unknown).
        """
        if self.status == "RELEASING" and self.next_airing_episode:
            return self.next_airing_episode - 1
        return self.episodes


# The fields every query needs. Search and the franchise walk differ only in
# the filter variables, so one fragment keeps the mappings in one place.
_QUERY = """
query ($search: String, $id: Int, $idIn: [Int], $page: Int, $perPage: Int, $type: MediaType, $isAdult: Boolean, $sort: [MediaSort]) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { currentPage hasNextPage }
    media(search: $search, id: $id, id_in: $idIn, type: $type, isAdult: $isAdult, sort: $sort) {
      id
      format
      episodes
      seasonYear
      status
      coverImage { large }
      description
      title { romaji english native }
      nextAiringEpisode { episode }
      synonyms
      relations {
        edges {
          relationType
          node { id format }
        }
      }
    }
  }
}
"""


def _media_from_node(node: dict) -> AniMedia:
    title = node.get("title") or {}
    relations = [
        (edge["relationType"], edge["node"]["id"], edge["node"].get("format") or "TV")
        for edge in (node.get("relations") or {}).get("edges") or []
        if edge.get("node")
    ]
    return AniMedia(
        id=node["id"],
        title_romaji=title.get("romaji") or "",
        title_english=title.get("english"),
        title_native=title.get("native"),
        synonyms=node.get("synonyms") or [],
        format=node.get("format") or "TV",
        episodes=node.get("episodes") or 0,
        season_year=node.get("seasonYear"),
        status=node.get("status") or "",
        cover_url=(node.get("coverImage") or {}).get("large"),
        description=node.get("description"),
        next_airing_episode=(node.get("nextAiringEpisode") or {}).get("episode"),
        relations=relations,
    )


def _gql(variables: dict) -> list[dict]:
    """POST one GraphQL query and return the Page.media nodes.

    Every failure — network, bad JSON, a GraphQL error — becomes a
    ProviderError so the API layer answers 400 rather than 500.
    """
    body = json.dumps({"query": _QUERY, "variables": variables}).encode("utf-8")
    # AniList's edge rejects bare urllib requests (403) unless a User-Agent is
    # sent. This is the one provider that needs it — the others don't, so the
    # header lives here rather than being added to every _get in the project.
    request = Request(
        API,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (unstream/self-hosted)",
        },
    )
    try:
        with urlopen(request, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except URLError as exc:
        raise ProviderError(f"Could not reach AniList: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ProviderError("AniList returned an unreadable response.") from exc

    if "errors" in data:
        message = "; ".join(e.get("message", "unknown error") for e in data["errors"])
        raise ProviderError(f"AniList API error: {message}")
    return (data.get("data") or {}).get("Page") or {"media": []}


def search(query: str, limit: int = 12) -> list[AniMedia]:
    """Free-text anime search. Adult content is excluded."""
    nodes = _gql(
        {
            "search": query,
            "type": "ANIME",
            "isAdult": False,
            "page": 1,
            "perPage": limit,
            "sort": ["SEARCH_MATCH", "POPULARITY_DESC"],
        }
    )
    return [_media_from_node(node) for node in nodes["media"]]


def get(media_id: int) -> AniMedia:
    """A single anime by its AniList id."""
    nodes = _gql({"id": media_id, "type": "ANIME", "page": 1, "perPage": 1})
    if not nodes["media"]:
        raise ProviderError(f"Anime {media_id} not found on AniList.")
    return _media_from_node(nodes["media"][0])


def _franchise_cached(media_id: int) -> list[AniMedia] | None:
    entry = _franchise_cache.get(media_id)
    if entry and time.monotonic() - entry[0] < _FRANCHISE_TTL_SECONDS:
        return entry[1]
    return None


def franchise(media_id: int) -> list[AniMedia]:
    """The connected TV chain around `media_id`, ordered oldest first.

    Walks SEQUEL/PREQUEL relations from the seed, keeps only TV entries
    (movies/ONAs/specials are not season structure and are dropped in v1),
    dedupes by id and sorts by (season_year, title). A standalone anime with
    no relations comes back as a single entry.

    A non-TV seed (a movie the user opened from search) is returned as a
    single-entry chain: a film is not a season, and its relations to the TV
    series must not drag the series' seasons into the movie's page.
    """
    cached = _franchise_cached(media_id)
    if cached is not None:
        return cached

    seed = get(media_id)
    if seed.format != "TV":
        # A movie / OVA / special opens as itself, never as the series it
        # belongs to. (Except a TV_SHORT — those are TV episodes, treat as TV.)
        if seed.format != "TV_SHORT":
            _franchise_cache[media_id] = (time.monotonic(), [seed])
            return [seed]

    seen: set[int] = {seed.id}  # the seed is already fetched
    by_id: dict[int, AniMedia] = {seed.id: seed}
    # Start the walk from the seed's relations, so its chain is still followed.
    frontier = [
        related
        for rtype, related, _fmt in seed.relations
        if rtype in ("SEQUEL", "PREQUEL")
    ]
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            media = get(current)
        except ProviderError:
            continue  # a dangling relation must not kill the whole walk
        by_id[current] = media
        for relation_type, related_id, _fmt in media.relations:
            # Continue through sequels and prequels; don't branch into
            # side-stories/spin-offs — those aren't "more seasons".
            if relation_type in ("SEQUEL", "PREQUEL"):
                frontier.append(related_id)

    # Aired seasons sort by (year, title); unreleased ones (year is None, e.g.
    # an announced sequel with no date yet) sort after them rather than leaping
    # to the front on year 0. TV entries are the "seasons" of a franchise; a
    # non-TV seed (a movie the user opened) still appears as its own single
    # entry so it opens rather than 404ing.
    chain = list(by_id.values())
    if not any(m.format == "TV" for m in chain):
        tv = chain
    else:
        tv = [m for m in chain if m.format == "TV"]
    ordered = sorted(
        tv,
        key=lambda m: (
            m.season_year is None,
            m.season_year or 0,
            m.best_title.lower(),
        ),
    )
    _franchise_cache[media_id] = (time.monotonic(), ordered)
    return ordered
