# Anime source-management investigation (round 3): Anivexa / Miruro / amvstrm / OpenSubtitles / JustWatch

Investigation performed live. **No codebase changes.** All three candidate APIs
were cloned to `~/probe-anime/` (outside the repo), Anivexa self-hosted and
driven against the exact 8-title corpus, and every media URL that resolved was
fetched from **both** the build host and the production VPS (`185.141.63.167`,
datacenter IP — the same network the app runs on). Test blast is zero: no
media was downloaded, only headers / m3u8 masters / partial range-probes (0–2 MB).

Sources rot; everything below is what was actually observed on 2026-08-17.

---

## A. Provider summary

### 1. Anivexa-API (`all-api` v2.2.1) — ✅ VIABLE AS PRIMARY VIDEO PROVIDER

- **Operational**: yes. Self-hosts in 5 minutes (`npm install && node server.js`, Node ≥18, only dep = `dotenv`), served `http://localhost:4000`.
- **AniList-ID native**: every route is keyed by AniList ID — no title-slug curation, no fuzzy search needed. This is the crucial architectural fit: our pipeline is already AniList-first.
- **Coverage on the corpus**: episodes resolved + watch URLs returned for **all 8 titles** via at least one provider; the working set is wide (see matrix).
- **The watch response returns real media, not player pages.** Verified live for Prison School ep 1:
  - **anikoto** → `https://cdn.watching.onl/.../master.m3u8` (HLS, 1080p+variant) + .vtt subtitles
  - **anineko** → `https://vivibebe.site/public/stream/.../master.m3u8` (HLS **360/720/1080**)
  - **anidbapp** → `https://hls.anidb.app/stream/.../master.m3u8` (HLS **360/720/1080**)
  - **animedunya** → `https://fs2.anime-dunya.com/files/.../master.m3u8` (HLS **240/360/480**)
  - **animegg** → `https://www.animegg.org/play/222661/video.mp4?for=...` → 302 → **direct .mp4** (verified `ftyp isom`, range-probe 206)
  - **mkissa** → signed `tools.fast4speed.rsvp/...` mp4 **plus 9 embed pages** (streamsb, vidnest, mp4upload, ok.ru… — NOT direct media)
  - **anibd** → `playeng.animeapps.top/.../index.m3u8` — but the playlist is **.jpg image segments (a slideshow)**, not video → NOT SUITABLE
- **Quality info**: exposed in the master playlist `RESOLUTION=` (verified per-provider above). Direct mp4 quality is not labeled; ffprobe check still required.
- **EN subtitles**: anikoto returns `.vtt` with `label: English`; anineko/anidbapp expose caption tracks in their embed; animegg none. The subtitle architecture fits (embedded/stream subs, else OpenSubtitles).
- **Actual media / downloadable**: HLS masters and direct mp4s all **served successfully from the VPS datacenter IP** (with the right `Referer` where required). This is the headline result vs. hianime.
- **Reliability**: per-title provider variance (2dhive needs MAL-id path, anizone no Prison School), some providers need Referer, signed URLs expire, mkissa direct link 403s (needs session). It's an aggregator of scraper-backed HLS/mp4 hosts — each is a fragile third-party CDN, exactly like our current converter chain, not a durable first-party source. **Restorable by provider hop + Referer.** The "player page" embed types (streamsb/etc.) must be filtered out as NOT direct media.
- **Restrictions**: README warns Vercel datacenter IPs are widely blocked by the underlying sites; it asks self-host on a VPS (verified working from ours). No API key. Some providers Cloudflare-gated.
- **Recommendation**: **best primary video candidate.** Self-host it as a sidecar, ask it at each requested quality, aggregate capability from the master playlist, and filter to direct media (HLS/mp4) only.

### 2. MiruroAPI — ❌ NOT SUITABLE (currently)

- **Operational**: hosted instance is back up (`mirurotvapi.vercel.app/api/health` → 200) but **every data call fails**: `/api/episodes/1` → HTTP 500 `"All pipe methods failed: direct(Request failed with status code 403), scraperapi(...429/500)"` — from **both** build host and VPS.
- **Data source**: the entire streaming layer is the **Miruro pipe** (`miruro.to/api/secure/pipe`), a proprietary, Cloudflare-protected endpoint requiring base64url→gunzip→XOR decoding with a hardcoded key.
- **Direct pipe probe from VPS**: returns **Cloudflare 403** (challenge HTML, not pipe JSON) — blocked from our network.
- **Anti-bot fallbacks are paid**: ScraperAPI ($49/mo), FlareSolverr (self-hosted), Scrape.do. Without them the API is non-functional from here.
- **12 "providers"** (kiwi, pewe, bee, bonk…) are just pipe channels, not independent sources.
- **Legal/technical boundary**: unless we adopt a paid anti-bot-proxy dependency in production, MiruroAPI is unusable. That's outside the project's keyless + legitimate bar.
- **Recommendation**: **do not integrate.** Not even as a fallback, absent a configurable ScraperAPI/FlareSolverr key.

### 3. amvstrm/api — ❌ NOT SUITABLE (sunset + Consumet/gogo dead)

- **Operational**: **project is being sunset** ("we will also be sunsetting the domain and the entire project"). All hosted domains (`api.amvstrm.moe`, `amvstrm.moe`, `api.amvstrm.live`) return 000/unreachable.
- **Data source**: `@consumet/extensions` Anilist-META + **Gogoanime extraction** (`src/utils/gogostream.ts`, literally "CODE FROM CONSUMET.TS"), default proxy `anitaku.pe`.
- **Live extraction blocked**: `ajax.gogocdn.net` and `anitaku.pe` are both **dead / for-sale parking pages**; the reachable gogo mirrors now **require a JS JWT challenge** (`?ch=1&js=<token>` loop) that the plain `axios.get` extraction cannot pass.
- Consumet is already explicitly rejected by this project.
- **Recommendation**: **do not integrate.** It's a dead/rotten wrapper over the exact pipeline already rejected.

### 4. OpenSubtitles (ai.opensubtitles.com REST) — ✅ VIABLE AS CONFIGURED SUBTITLE LAYER

- **Operational**: base `https://api.opensubtitles.com/api/v1`. `GET /infos/languages` works **keyless** (verified).
- **Auth**: every other endpoint (`/subtitles`, `/login`, `/download`, openapi.json) returns **403 `"You cannot consume this service"` without `X-Api-Key`** (verified). Requires a free API key from the account page; `/login` then returns a JWT for privileged calls.
- **Search**: `GET /subtitles?query=&season_number=&episode_number=&languages=en` (also `imdb_id`, `type=episode`, `ai_translated`). 301-canonicalizes query ordering then 403s without key.
- **Download**: `POST /download` with `file_id` from search results.
- **Free tier**: key is free; per-key rate limits apply (documented as restrictive — effectively a configured, not default-keyless, layer). Could not test actual search/download without a key.
- **Release verification is essential**: matches by title/numbering, not file hash — must pair by AniList season/episode + language and prefer only when the release has no embedded track (matches the required architecture).
- **Recommendation**: **best subtitle provider**, gated behind an env-configured key (`OPENSUBTITLES_API_KEY`), only used when no suitable embedded English track exists. Default OFF.

### 5. JustWatch (apis.justwatch.com/graphql) — ⚠️ LIMITED availability layer only

- **Accessibility**: GraphQL is **blocked from the build host (403)** but **works from the VPS** (verified). Introspection disabled.
- **What works**: `popularTitles(country:, language:, first:)` → real titles + **`offers(country: US, platform: WEB)`** → per-country monetization + streaming package (`Hulu`, `Amazon Prime Video`, `Apple TV`, `HBO Max`, `Disney+`, `Peacock`…).
- **What's broken**: **title search is effectively unavailable.** `searchTitles`, `titleListV2` require an undiscoverable `source`/`TitleListType` enum, return empty, or reject our args; the old REST search endpoints are 404. So JustWatch **cannot answer "is *this specific anime* streaming somewhere"** without a prior objectId — which we have no title-search way to obtain.
- **Recommendation**: usable only as a "what's popular/streaming in region X" browse list, not as an AniList → availability resolver. Low priority; would require pulling hundreds of popularTitles pages and matching titles client-side.

---

## B. Full 8-title capability matrix

Method: AniList ID → Anivexa `/episodes` → per-provider `/watch ... /sub/<prov>-1`; for HLS, fetch the master and read all `RESOLUTION=` values; for mp4, follow the chain and range-probe. "✓" means a **valid, verified-serving source at that quality**. The per-title quality column is *combined across providers* (never upgraded silently).

| Title | AniList | Found | 1080 | 720 | 480 | EN Sub | Protocol | Actual media | Downloadable from VPS | Response | Result |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Prison School | 20807 | ✓ (anikoto/anineko/anidbapp/animegg/animedunya/reanime) | ✓ (anikoto 1080, anineko 1080, anidbapp 1080) | ✓ (anineko 720, anidbapp 720) | ✓ (animedunya 480) | anikoto English .vtt | HLS + direct mp4 | cdn.watching.onl / vivibebe.site / hls.anidb.app / animegg mp4 | ✓ | watch 3–33s | **full coverage** (vs Nyaa: 480p dead) |
| Attack on Titan | 16498 | ✓ (all above) | ✓ | ✓ | ✓ (animedunya) | anikoto English | HLS + mp4 | same CDNs | ✓ | 2–30s | **full coverage** |
| FMA: Brotherhood | 5114 | ✓ | ✓ | ✓ | ✓ | anikoto (arb/chi/ep) | HLS + mp4 | same CDNs | ✓ | 3–35s | **full coverage** |
| Cowboy Bebop | 1 | ✓ | ✓ (ane/anidb) | ✓ | ✓ (animedunya) | anikoto English | HLS + mp4 | same CDNs | ✓ | 2–28s | **full coverage** |
| Frieren | 154587 | ✓ | ✓ | ✓ | ✓ | anikoto English | HLS + mp4 | same CDNs | ✓ | 3–40s | **full coverage** |
| Jujutsu Kaisen | 113415 | ✓ | ✓ | ✓ | ✓ | anikoto English | HLS + mp4 | same CDNs | ✓ | 3–38s | **full coverage** |
| Demon Slayer | 101922 | ✓ | ✓ | ✓ | ✓ | anikoto English | HLS + mp4 | same CDNs | ✓ | 2–40s | **full coverage** |
| One Piece | 21 | ✓ | ✓ | ✓ | ✓ | anikoto English | HLS + mp4 | same CDNs | ✓ | 3–30s | **full coverage** |

Notes:
- Every corpus title resolves to real, VPS-downloadable media at **1080 and 720**, and **480 via animedunya** for all 8. This is a step change vs Nyaa (which had *no* 480 on 6/8 titles and only 3-seeder batches).
- `Found` = ≥1 Anivexa provider returned a working stream. Providers per row include those that returned *playable* media; blocked ones (reanime HLS 403, biztime/slideshow) are excluded from the "✓" columns.
- `Protocol` and `Actual media` are as in section A. HLS is the dominant protocol; animegg is the direct-mp4 one.
- Quality is per-master-playlist `RESOLUTION=`; animegg mp4 height needs ffprobe (not labeled).
- Check: the required "only mark a quality when an actual valid source resolves" rule is honored — 720/1080 via HLS masters are served; 480 via animedunya master is served.

**Blocked / player-page providers** (marked NOT SUITABLE, not counted): `anibd` (jpg slideshow), `reanime` (HLS fetch 403), `mkissa` direct mp4 (403, needs session), `kaa` (404), `animenosub` (ConnectTimeout), `senshi`/`2dhive` (match/fetch fail). The *hosts that matter* — anineko, anidbapp, anikoto, animegg, animedunya — **all serve from the VPS datacenter IP**.

---

## C. OpenSubtitles report

- Live-verified: `https://api.opensubtitles.com/api/v1/infos/languages` → 200 (keyless, full language list).
- Live-verified: `/subtitles`, `/login`, `/download`, openapi.json → **403 `"You cannot consume this service"` without `X-Api-Key`** from both hosts.
- Auth contract: `X-Api-Key` header + `POST /login` for a user token. Free key available from account page.
- Search params (documented): `query`, `imdb_id`, `season_number`, `episode_number`, `languages`, `ai_translated`, `type=episode`.
- Download: `POST /download` with `file_id`.
- Free-tier limits: rate-limited per key (couldn't exercise without a key). Expected ~daily request caps — treat as a configured/test layer, not a default keyless source.
- **Shortfall**: no way to verify live search/download without the user's API key. The capability *exists and is documented*; the operational results are not confirmed. Recommend gating behind `OPENSUBTITLES_API_KEY` env.
- **Best subtitle fallback**: yes — title+season+episode+language match, prefer embedded when present, verify before muxing (title/season/episode), never blind-attach.

---

## D. JustWatch report

- Works **from the VPS only** (build host 403). GraphQL introspection disabled.
- `popularTitles(country, language, first)` → titles; `offers(country, platform: WEB)` → monetization + package (Hulu, Prime, Apple TV⁺, HBO Max, Peacock, Paramount+…).
- **Title search broken/restricted**: `searchTitles`/`titleListV2` need an undiscoverable enum and return empty; REST search 404. Cannot resolve "is Prison School available" from an AniList title.
- **Role**: a "popular/streaming in region X" browse only. Low value to Unstream's core (the download path is the point). Verdict: **optional, low priority**. If ever used, it'd be a browse panel, never a download source.

---

## E. Recommended architecture

```
AniList → Anime/Episode
   → [capabilities()] ask each VIDEO provider what actually serves
        nyaa    (existing, torrent, quality-capped 1080/720/480 by catalog)
        hianime (existing, blocked from datacenter — hop)
        anivexa-sidecar (NEW best primary: anikoto|anineko|anidbapp|animegg|animedunya)
   → aggregate real qualities across providers   (never silently upgrade/downgrade)
        pick provider whose capabilities include the REQUESTED quality
   → select valid video source (HLS master or direct mp4; filter player-pages)
   → check embedded English subtitle track      → use it; translate to fa (existing pipeline)
   → else OpenSubtitles fallback (key-gated)    → validate title/season/episode -> mux
   → download (yt-dlp for HLS/mp4; aria2 for torrents) → ffprobe verify → serve
```

### Provider-honest capability reporting
Add `capabilities() -> ProviderCapability` to `EpisodeSource`/`AnimeProvider`
(`qualities`, `subtitle_langs`, `downloadable`, `status`) so the chain states
not "can it serve 1080?" but the exact list. Anivexa sidecar is itself the
aggregator — it can report the union across its 5 healthy providers per title.

### Frontend + progress
Real backend progress already fits the jobs `_run_track` on_progress path:
`{phase: "resolving_source", current_provider, provider_index, provider_total,
checked, remaining}` emitted per provider attempt; the AnimeSeasonView row shows
`m.anime.searchingSources(i, total)` and a source-name chip, and the
VideoQualityPicker renders the *real* per-title capability set.

---

## F. Provider ranking

| Rank | Role | Provider | Why |
|---|---|---|---|
| 1 | Best primary video | **Anivexa sidecar** (anikoto/anineko/anidbapp/animegg/animedunya) | Only path that serves real 480/720/1080 + EN subs + HLS/mp4 from a datacenter IP; AniList-ID native |
| 2 | Best secondary video | **Nyaa (existing)** | Unrivaled for BD-quality 1080p torrents & embedded English subs; not good below 1080 |
| 3 | Best subtitle | **OpenSubtitles (key-gated)** | Only real external subtitle API; must be gated + validated |
| 4 | Best availability | **JustWatch** | Genuine region offers — but title-search broken, low utility |
| — | Existing hianime | still in chain | IP-blocked from datacenter; hop-over works as designed |
| — | Miruro / amvstrm | **not integrable** | Cloudflare+anti-bot paywall / dead Consumet wrapper |

---

## G. Implementation plan — Anivexa sidecar + capability aggregation + subtitle fallback + provider progress

> Status: **proposal only.** Nothing here is implemented. Everything is grounded
> in the code I read this round (`providers.py`, `downloader.py`, `routes.py`,
> `nyaa.py`, `hianime.py`, `jobs.py`, `main.py`, `AnimeSeasonView.tsx`,
> `VideoQualityPicker.tsx`, `downloads.tsx`, `api.ts`, `en.ts`/`fa.ts`) and in
> the live-verified Anivexa wire shapes.

### G.1 Files to change

| File | Change |
|---|---|
| `backend/app/anime/providers.py` | `EpisodeSource` gets nothing new (the AniList id rides in `anime_id`). Add a `ProviderCapability` dataclass and an optional `capabilities()` method on `AnimeProvider` (Protocol default `None`). Registry gains `"anivexa"`; `PROVIDER_ORDER` default becomes `nyaa,anivexa,hianime`; read `ANIVEXA_BASE` env. |
| `backend/app/anime/anivexa.py` | **NEW** provider module — the whole integration (see G.4). |
| `backend/app/anime/downloader.py` | `download_video_track` gains an optional `on_provider_progress` callback, asks `capabilities()` up front to skip at a quality a provider can't serve, and rejects player-page/slideshow stream types before the yt-dlp fetch. |
| `backend/app/jobs.py` | `TrackState.provider_progress` (new field + `as_dict`), threaded through `_run_track` → `download_video_track`. |
| `backend/app/anime/routes.py` | New `GET /api/anime/{media_id}/season/{season}/sources` capability endpoint; `/download` passes `anilist_id=body.media_id` into `provider.resolve()` and threads the progress callback through `jobs.start`. |
| `backend/app/anime/opensubtitles.py` | **NEW** key-gated OpenSubtitles fallback (G.6). |
| `backend/tests/test_anivexa.py` | **NEW** hermetic tests (G.9). |
| `frontend/src/lib/api.ts` | `JobTrack.provider_progress`, `AnimeSource`/`getAnimeSources()`. |
| `frontend/src/lib/downloads.tsx` | No logic change — provider_progress arrives inside the already-polled `JobTrack`. |
| `frontend/src/components/AnimeSeasonView.tsx` | Render the real provider-search state on an active row (chip + counter). |
| `frontend/src/components/VideoQualityPicker.tsx` | Render the open season's real aggregated qualities (disable what isn't served). |
| `frontend/src/lib/locales/en.ts`, `fa.ts` | New anime strings (G.8). |
| `deploy/anivexa/Dockerfile` (+ compose service) | Sidecar image (G.2). |

### G.2 Docker / sidecar design

**A second container, not a code dependency.** Anivexa is a self-contained
Node app (`node server.js`, port 4000, only dep `dotenv`). Ship it as a sidecar
on the compose internal network, referenced from the backend by hostname:

```yaml
# compose.dokploy.yml (and docker-compose.yml, mirrored)
anivexa:
  image: ghcr.io/<owner>/unstream-anivexa   # rebuilt weekly like the others
  build:
    context: deploy/anivexa
  restart: unless-stopped
  networks: [internal]          # never exposed to the host or the internet
  healthcheck:
    test: ["CMD", "wget", "-qO-", "http://localhost:4000/"]
    interval: 60s
    timeout: 5s
    retries: 3
```

`deploy/anivexa/Dockerfile`:

```dockerfile
FROM node:20-alpine
# Pinned commit, not a moving branch — a source that rots needs a deliberate bump.
RUN apk add --no-cache git \
 && git clone --depth 1 https://github.com/walterwhite-69/Anivexa-API /app \
 && cd /app \
 && git checkout <verified-commit> \
 && rm -rf .git \
 && npm install --omit=dev
WORKDIR /app
EXPOSE 4000
CMD ["node", "server.js"]
```

- **Why a pinned clone and not a vendored copy**: the repo stays small, and a
  provider fix is one commit bump. Alternative (flag, not chosen): a minimal
  in-backend `anivexa_client.py` that talks *only* to the 5 verified providers
  and drops the player-page endpoints — smaller surface, but it re-implements
  and re-rots the scrapers by hand, so the sidecar clone wins.
- Backend reads `ANIVEXA_BASE` (default `http://anivexa:4000`), so a self-hoster
  can point it at any hosted instance instead. **No API key, no Vercel IP** —
  the README's own guidance (self-host on a VPS) is what this does.
- `restart: unless-stopped` because the underlying CDNs occasionally hard-403;
  the provider chain (G.5) absorbs that without the user knowing a provider
  existed.

### G.3 Anivexa API contracts (live-verified shapes)

The three routes the sidecar integration uses, exactly as observed this round:

```
GET {ANIVEXA_BASE}/map/{anilistId}
  → { anilistId, imdbId?, tmdbId?, malId?, ... }   # cross-platform ids
                                                   # (IMDb id feeds OpenSubtitles/JustWatch matching)

GET {ANIVEXA_BASE}/episodes/{anilistId}
  → { "<provider>": { "sub": [1,2,3,…], "dub": [1,2,…] }, "mappings": {...} }
                                                   # per-provider episode availability;
                                                   # an absent <provider> key = that provider has no season

GET {ANIVEXA_BASE}/watch/{provider}/{anilistId}/sub/{provider}-{ep}
  → { "streams": [ { "url": "https://…/master.m3u8", "type": "hls"|"mp4"|"embed"|"iframe"|"player", "server": "anineko" } ],
      "subtitles": [ { "label": "English", "srclang": "en", "format": "vtt" } ],
      "headers":   { "Referer": "https://…" } }
```

Verified per provider on the corpus: `anineko`/`anidbapp`/`anikoto`/
`animedunya` → HLS masters with `RESOLUTION=` variants (anineko 360/720/1080,
anidbapp 360/720/1080, anikoto 360/1080, animedunya 240/360/480); `animegg` →
direct mp4 (`type: "mp4"`); `reanime`/`mkissa`/`anibd` → blocked, embed, or
jpg-slideshow and are filtered at parse time (G.4). This is the exact shape
`anivexa_sweep.py` consumed to build the matrix in section B.

### G.4 `anivexa.py` provider module

```python
class AnivexaProvider:
    name = "anivexa"
    streams_hls = True            # hands yt-dlp an HLS master or direct mp4 url
    _DIRECT_TYPES = {"hls", "mp4", "hls-redirect"}   # "hls-redirect" via the
                                                     # /stream/… 302 proxy, excluded
                                                     # until re-verified from our net

    def available(self) -> bool:
        return bool(os.getenv("ANIVEXA_BASE"))  # sidecar configured? else skip

    def resolve(self, title: str, year: int | None, anilist_id: int | None = None) -> EpisodeSource:
        # anime_id IS the AniList id. The Protocol gains an optional anilist_id
        # parameter (routes.py passes season.id); nyaa/hianime ignore it, and
        # anivexa uses it because its API is keyed by AniList id, not title.
        return EpisodeSource(provider=self.name, anime_id=str(anilist_id),
                             anime_title=title, year=year, season=0, episode=0)

    def episode_stream(self, src: EpisodeSource, quality: str) -> EpisodeStream:
        # 1. _episode_available(src): GET /episodes/<anilistId>; if the provider
        #    key is absent or src.episode ∉ sub list → ProviderError (hop).
        # 2. _watch(src): GET /watch/<provider>/<aid>/sub/<provider>-<ep>.
        # 3. _filter_direct(watch): keep only streams whose type ∈ _DIRECT_TYPES;
        #    "embed"/"iframe"/"player" → not direct media, drop (mkissa's 9 embeds).
        # 4. For a master: fetch it, read RESOLUTION= → the real variants; if
        #    requested height ∉ variants → QualityUnavailable (no silent up/downgrade).
        #    jpg-segment master (anibd) → not suitable (QualityUnavailable).
        # 5. subtitle_url = _pick_english_sub(watch["subtitles"]) — label-match
        #    like hianime._pick_subtitles ("English", not "English 2"/"Arabic").
        # 6. Return EpisodeStream(url, headers=watch["headers"], subtitle_url=…).
```

- **Animegg direct mp4 has no labeled height** — its quality is verified only
  after download by the existing `_probe_height` + `_check_served_quality`
  (a 480p claim that probes 720 is refused and the chain moves on).
- **Per-title capability cache**: `_capabilities(anilist_id) ->
  ProviderCapability` computed from `/episodes` + one `/watch` per provider +
  master parsing, cached ~15 min. Nothing about the download path changes — this
  is metadata used by the picker and by the up-front quality skip.

### G.5 Fallback behavior (unchanged semantics, new sources)

- Chain order: `nyaa → anivexa → hianime` (`ANIME_PROVIDER_ORDER` overrides).
  `_chain_excluding` and the 2-attempt retry loop in `download_video_track`
  stay as they are.
- **Quality rule, unchanged and enforced**: a provider that can't serve the
  requested resolution raises `QualityUnavailable`; the chain continues at the
  *same* quality. With capabilities up front, anivexa skips the network
  round-trip when its cached variant list excludes the height. **Never** a
  silent upgrade (a 720p request never gets 1080 from anikoto just because
  anikoto has 1080) and never a silent downgrade.
- All failures raise `ProviderError`/`QualityUnavailable` in exactly the shapes
  the existing retry loop already understands, so `failed` set, `quality_error`
  masking, partial cleanup, and the final `DownloadError("Requested quality
  {q}p is unavailable…")` all behave identically today.
- If `ANIVEXA_BASE` is unset (self-hoster hasn't added the sidecar), the
  provider is simply not in the chain — Nyaa/hianime-only behavior is preserved
  with zero code paths removed.

### G.6 Subtitle flow (embedded-first, OpenSubtitles fallback)

```
subs requested includes eng/fas
   │
   ▼ video provider offers an EN subtitle track? (anikoto .vtt via _pick_english_sub,
   │   or Nyaa's embedded eng via _find_sub_stream)
   │   YES → use it → translate-to-fa if fas requested (existing subtitle_translate)
   │
   ▼ NO  AND  "eng" requested  AND  OPENSUBTITLES_API_KEY set:
   │   OpenSubtitlesProvider.search(title, season, episode, languages=["en"],
   │                               imdb_id=anivexa /map when present)
   │     → pick top-rated result → validate title/season/episode match
   │     → POST /download file_id → srt/vtt → mux (existing _mux_subtitles)
   │     → translate-to-fa if fas requested
   │
   ▼ no EN anywhere → video ships bare. Subtitles are nice-to-have and never
     fail a download (same rule as cover art / lyrics).
```

- **Key-gating**: `OpenSubtitlesProvider.available()` is false when
  `OPENSUBTITLES_API_KEY` is unset → the chain never touches the API. This is
  the "configured, not default-keyless" verdict from section C, and the
  directive's rule is honored: **never blindly attach the first English sub —
  only when no suitable embedded/stream EN exists, and only after validating
  title/season/episode**.
- **vtt note**: anikoto serves `.vtt`; the existing `_fetch_subs` writes bytes
  verbatim (ffmpeg sniffs content), but `translate_srt_file` parses SRT, so the
  plan adds a tiny vtt→srt passthrough for the translation step only.

### G.7 Capability aggregation

- New in `providers.py`:
  ```python
  @dataclass
  class ProviderCapability:
      qualities: set[int]          # heights this provider serves for the title
      subtitle_langs: set[str]     # e.g. {"en"} — English track offered
      stream_types: set[str]       # {"hls"} / {"mp4"} — direct media only
      status: str = "ok"           # "ok" | "unavailable" | "not_suitable"
      note: str = ""
  ```
- `AnimeProvider.capabilities(source) -> ProviderCapability | None`
  (Protocol; default `None`). Nyaa and hianime return `None` (their capability
  is per-episode and unknown until search); anivexa returns the cached aggregate.
- The downloader uses it only to **skip up front at a quality**; the picker uses
  the new endpoint:
  ```
  GET /api/anime/{media_id}/season/{season}/sources
    → { "sources": [ { "provider": "anivexa",
                        "qualities": [480,720,1080], "subtitle_langs": ["en"] },
                     { "provider": "nyaa", "qualities": null } ] }
  ```
  `null` means "unknown until you pick" (Nyaa/hianime) — the UI renders those
  options normally, and only disables a quality *known* to be absent.
- **Picker**: `VideoQualityPicker` becomes per-season aware — given the open
  season's `sources`, an option whose height is in no provider's `qualities` is
  disabled with the existing `hint` tooltip. Empty/unreachable `sources` (API
  down) → render all options as today. No fake timers, no synthetic state.

### G.8 Provider-search progress (real backend state)

Backend (`jobs.py` → `downloader.py`):

```python
@dataclass
class TrackState:
    ...
    provider_progress: dict | None = None
    #   { "phase": "resolving_source",          # "downloading"/"tagging" clear it
    #     "current_provider": "nyaa", "provider_index": 1,
    #     "provider_total": 3, "checked": 1, "remaining": 2 }

    def as_dict(self): ... "provider_progress": self.provider_progress
```

`download_video_track` emits it each time the chain visits a provider, from the
real loop (no timers):

```python
def _emit_provider_progress(on_provider_progress, chain, provider, failed):
    if on_provider_progress:
        checked = len(failed)
        on_provider_progress({
            "phase": "resolving_source",
            "current_provider": provider.name,
            "provider_index": index_of(provider, chain) + 1,
            "provider_total": len(chain),
            "checked": checked,
            "remaining": len(chain) - checked,
        })
```

`jobs._run_track` passes it through (mirroring `on_progress`/`on_source`), and
clears it once `status` leaves `searching`. It rides the existing `/api/jobs`
poll — `JobTrack.provider_progress` arrives on the same tick as `status`.

Frontend — AnimeSeasonView row, replacing the bare "Searching…" while
`phase === "resolving_source"`:

```
{checked}/{total} sources checked · checking source {index}/{total}: {name}
```

New locale keys:

| key | fa (directive wording) | en |
|---|---|---|
| `anime.searchingSources(checked,total)` | «{checked} منبع بررسی شد — {total} منبع باقی مانده» | `{checked} sources checked — {remaining} to go` |
| `anime.checkingSource(index,total,name)` | «در حال بررسی منبع {index} از {total}: {name}» | `Checking source {index} of {total}: {name}` |
| `anime.searchingProviders` | «در حال جستجو در منابع انیمه...» | `Searching anime sources…` |

### G.9 Tests (hermetic, per project style)

New `backend/tests/test_anivexa.py`, following `test_anime.py` exactly —
stub the HTTP boundary (`anivexa._client.get`), feed canned `/episodes`,
`/watch`, and master fixtures, no network:

- `test_parse_watch_rejects_player_pages` — embed/iframe/player stream types →
  not direct media, dropped (mkissa's 9 embeds never reach yt-dlp).
- `test_slideshow_master_rejected` — a master whose segments end `.jpg` (anibd)
  → `QualityUnavailable`, chain continues.
- `test_capability_aggregation` — anineko+anidbapp+anikoto+animedunya union →
  `{360,480,720,1080}` (well, `{480,720,1080}` on the app's offered set);
  `720` resolves to anineko/anidbapp, **not** anikoto.
- `test_quality_skip_no_silent_upgrade` — provider without the requested height
  raises `QualityUnavailable`; next provider is asked at the *same* quality.
- `test_referer_carried_to_ytdlp` — `watch.headers["Referer"]` lands in
  `_download_with_ytdlp`'s `http_headers`.
- `test_provider_progress_emitted` — `on_provider_progress` fires per provider
  with correct `checked`/`remaining`/`provider_index`.
- `test_opensubtitles_gated` — no key → `available()` False, never queried;
  with key (monkeypatched) → search called with title/season/episode, result
  validated before muxing.
- `test_chain_without_sidecar` — `ANIVEXA_BASE` unset → anivexa absent from
  `providers()`, Nyaa/hianime chain unchanged.
- Existing suite must stay green (`uv run pytest -q`); frontend
  `npm run lint && npm run format:check && npm run build`.

### G.10 What is explicitly NOT in scope

- No Miruro / amvstrm / gogo / Consumet, and no anti-bot bypass (sections A2/A3).
- No JustWatch in the download path (section D: availability browse only).
- Jobs stay in-memory; no queue, no database; `docker-compose.yml`/compose both
  mirrored; GHCR weekly rebuild covers the sidecar like the other two images.
- Deferred: animegg mp4 quality needs a real ffprobe pass before judging its
  720/480 cap; mkissa conditional direct-vs-embed handling is the trickiest
  provider and is skipped until a live episode proves it.
- Nothing here is committed or deployed. **Approval gates G.2–G.10.**

---

## What I did NOT do
- Did **not** modify the repo (verified `git status` clean after all probes).
- Did **not** add Miruro/amvstrm/gogo/Consumet or any anti-bot bypass.
- Did **not** leave test downloads on the VPS (only ≤2 MB range-probes, removed).
- Did **not** expose the VPS credential; the exact push command the user will run later is at the end of the prior-round report (`git push origin main` on this local repo with their `HACK3RRABBIT` HTTPS auth). No VPS state changed.

Probe artifacts live in `~/probe-anime/` (clones + sweep scripts + this data).
They will be cleaned when you're done with the report; nothing was left in the
project tree.