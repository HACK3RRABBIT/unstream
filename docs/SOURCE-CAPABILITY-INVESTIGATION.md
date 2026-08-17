# Anime source-capability investigation (Aug 2026)

Investigation performed live on the test VPS (`185.141.63.167`, same container
network + code as production). The goal was to understand *why* anime source
coverage is title-dependent — 1080p sometimes works, 720p/480p frequently
fail, and some shows (Prison School) fail at every requested quality — before
any implementation. **No downloader/provider code was changed.**

The transport fix (`aria2c`, commit `b0ff3cc`) is deployed and verified. This
doc records the *source* picture on top of it.

---

## 1. Provider / source capability matrix

Method: for each title, probe AniList, then each provider in the configured
chain (`nyaa,hianime`), asking **exactly what each source can serve** per
episode — without downloading media. For Nyaa, that means the actual release
titles the search returns, their claimed resolution, seeders, and single-vs-
batch shape. For hianime, the m3u8 master and its variant heights.

### 1a. What actually resolves, per title (episode 1)

| Title | AniList | Nyaa 480p | Nyaa 720p | Nyaa 1080p | hianime |
|-------|---------|-----------|-----------|------------|---------|
| **Prison School** | 20807, FINISHED, 12 eps | **none** | ✓ batch (3 seeders) | ✓ batch (3-4 seeders) | unreachable |
| **Attack on Titan** | 16498, FINISHED, 25 eps | none | ✓ | ✓ | unreachable |
| **Fullmetal Alchemist: Brotherhood** | 5114, FINISHED, 64 eps | none | ✓ | ✓ | unreachable |
| **Cowboy Bebop** | 1, FINISHED, 26 eps | none | ✓ | ✓ | unreachable |
| **Frieren** | 154587, FINISHED, 28 eps | none | **none** | ✓ | unreachable |
| **Jujutsu Kaisen** | 113415, FINISHED, 24 eps | none | **none** | ✓ | unreachable |
| **Demon Slayer** | 101922, FINISHED, 26 eps | none | ✓ batch | ✓ | unreachable |
| **One Piece** | 21, RELEASING, 0 planned | ✓ | ✓ | ✓ | unreachable |

Every title resolves on Nyaa for at least one quality. **No title is a total
source failure** — the data source has the show.

### 1b. Nyaa release detail for the failing qualities

The single most important finding. When a quality "fails," the reason is
almost never "no such release exists." It is that **the app's episode filter
drops the well-seeded releases and only keeps per-episode or batch-named
ones** — and for the lower qualities, the kept ones are frequently 0-seeder
or dead-swarm batches.

**Prison School — the full Nyaa page for `Prison School 1` (top releases):**

| Release | Seeders | Resolution | _parse_rows keeps? |
|---------|---------|-----------|--------------------|
| `[Reaktor] Prison School + OVA Uncensored v2 [1080p][BD][x265]…` | 98 | 1080 | **DROPPED** (no per-ep number / no range) |
| `[FLAV1N] Prison School - S01+OVA (BD 1080p 10bit AV1 Opus)…` | 56 | 1080 | **DROPPED** |
| `[Tenrai-Sensei] Prison School S1+OVA [BD][1080p][HEVC x265]…` | 49 | 1080 | **DROPPED** |
| `[Chihiro] Prison School [Blu-ray 1080p Hi10P FLAC]` | 29 | 1080 | **DROPPED** |
| `Prison.School.S01.1080p.BluRay.Remux…Humble` | 23 | 1080 | **DROPPED** |
| `[Mysteria] Prison School+OVA (BD 1080p Hi10 FLAC)` | 13 | 1080 | **DROPPED** |
| `[Polarwindz] Prison School (Uncensored BD 1080p HEVC Opus)` | 12 | 1080 | **DROPPED** |
| `Prison.School.S01.1080p.Blu-Ray…iAHD` | 8 | 1080 | **DROPPED** |
| `[HorribleSubs] Prison School (01-12) [720p] (Batch)` | **3** | 720 | **KEPT** (range 1-12) |
| `[HorribleSubs] Prison School (01-12) [1080p] (Batch)` | **3** | 1080 | **KEPT** (range 1-12) |
| …74 more rows, almost all 0 seeders | | | |

The 8 well-seeded, English-subbed 1080p releases (98, 56, 49, 29, 23, 13, 12,
8 seeders) are **all dropped** by `_parse_rows` because their titles carry no
per-episode number and no parseable numeric range ("S1+OVA", "Vol.1", "S01").
The app only keeps the two 3-seeder HorribleSubs **batches** — which is why
every quality either fails (480p: the 480p batch has **0 seeders**, and the
single-episode 480p files are all 0-seeder) or resolves to a slow 3-seeder
batch.

**The same pattern across the "720/480 fail" titles:**

| Title | Top kept release | Top *dropped* release |
|-------|------------------|----------------------|
| **Jujutsu Kaisen** | *none* (all top releases dropped) | `[ToonsHub] …S03E10…` **795** seeders, dropped |
| **Frieren** | `[SubsPlease] …S2-01…` 760 seeders | `[Erai-raws] …-10…` **947** seeders, dropped |
| **Attack on Titan** | `[Trix] (Complete Series…)` 564 / `[TatakaeFuniSubs] S01-04` 173 | `[Anime Time] (Complete Collection)` **757** seeders, dropped |
| **Demon Slayer** | `[Trix] Kimetsu no Yaiba S01-05` 480 | `Demon Slayer…Infinity Castle 2025` **562** seeders, dropped |

The dropped releases are *not* "wrong" — they are the well-seeded, complete
collections. The filter is conservative on purpose (a missed batch falls
through to the next provider; a false positive breaks a single download), but
it is **too conservative** for the top-seeded catalog.

### 1c. hianime

`hianime.to` is **unreachable from both this build host and the VPS** — the
read timeout is a provider-level per-IP block (TLS handshake succeeds, then
the HTTP response never arrives). This is the known datacenter-IP block, not
our resolver. The provider is correctly marked "rotten" and hopped over by
the chain, exactly as designed.

---

## 2. Prison School — complete pipeline trace

Run live on the VPS via `prison_trace.py` (metadata only, no media pulled).

| Stage | Result |
|-------|--------|
| AniList search | `Prison School`, id 20807, FINISHED, 12 eps, 2015 |
| AniList franchise | 1 season, title `Prison School` |
| Provider plan (Nyaa resolve) | `EpisodeSource(anime_id='Prison School')` |
| **480p** | `QualityUnavailable: No 480p release of 'Prison School' episode 1 on Nyaa.` |
| **720p** | FOUND `[HorribleSubs] (01-12) [720p] (Batch)`, 3 seeders, 5.1 GiB |
| **1080p** | FOUND `[HorribleSubs] (01-12) [1080p] (Batch)`, 4 seeders, 8.8 GiB |
| **original** | FOUND `[HorribleSubs] (01-12) [1080p] (Batch)` (best-seeded) |
| Batch file list | 12 files `[HorribleSubs] Prison School - NN [720p/1080p].mkv` |
| `_file_is_episode(…, 1)` | **matches file index 1** correctly |
| aria2 `--select-file=1` | works; swarm is **ALIVE** |

**Decisive swarm-liveness test** (capped, non-destructive — replicates the
app's exact aria2 path but with `--max-download-limit=200K` and a ~75s wall):

| Torrent | Result |
|---------|--------|
| 720p batch, file 1 | aria2 connected to **3 peers**, downloaded **12 MiB (2%) in ~1 min at ~180 KiB/s**, then stopped only because the test capped it |
| 1080p batch, file 1 | connected to 5 peers, slow but **alive** (preallocated the full file) |

**Conclusion: Prison School is NOT a dead source.** The 720p and 1080p swarms
seed, the torrent client works, `_file_is_episode` finds the right file. It
fails because:

1. **480p genuinely does not exist** with seeders — the 480p batch and single
   files are all 0-seeder (dead).
2. **720p/1080p only resolve to a 3-seeder HorribleSubs batch**, because the
   well-seeded releases (98, 56, 49, …) are dropped by the episode filter.
   At ~180 KiB/s, a 5 GiB batch extraction (even of one 439 MiB file) takes
   ~40 minutes at the swarm's real speed — and the app's jobs has no stall
   timeout, so a job that *looks* stuck is often just slow.

So "fails at every quality" = (a) the one missing quality is genuinely absent,
(b) the quality that exists is only available via a slow, low-seeded batch
that the app's conservative filter *selected*, while the fast releases were
filtered out.

---

## 3. 1080 vs 720/480 — root cause

The behavior is **not a single bug**; it is three interacting facts:

1. **Nyaa releases the top catalog almost exclusively at 1080p.** The
   well-seeded singles and collections for modern shows (Jujutsu Kaisen,
   Frieren, Attack on Titan) are 1080p WEB-DL/BD. A 480p option rarely exists
   at all, and 720p often only as an older or 0-seeder release. So "720/480
   fails" is **source-side** for the mainstream catalog.

2. **Our episode filter drops the well-seeded releases.** For older/completer
   shows (Prison School, FMA:B, older seasons) the top releases are
   volume-named collections ("S1+OVA", "Complete Series") that `_parse_rows`
   drops, leaving only low-seeded per-episode or batch releases. This is the
   **title-dependent** part — Prison School's best catalog is invisible.

3. **The quality selector and post-download check are actually working.**
   The `_format_selector` strictness, `_check_served_quality` ffprobe check,
   and `QualityUnavailable` propagation all behave correctly. The mislabeled
   release we hit earlier (AoT S1E01 "720p" that was actually 2809p) was a
   *correct* refusal — that's the system doing its job.

| Diagnosis option | Verdict |
|------------------|---------|
| Source genuinely only provides 1080p | **Yes, mostly** for the mainstream catalog |
| Source provides 720/480 but our selector rejects it | **Sometimes** — but via filter-drop, not selector |
| Source quality labels inconsistent | Yes — mislabeled releases exist, and `_check_served_quality` catches them |
| Source URLs expire | Not applicable to Nyaa torrents (magnet/hash), and hianime is blocked |
| Source is HLS and downloader handles it wrong | Not applicable — hianime (HLS) is blocked; Nyaa (torrent) works |
| Episode mapping is wrong | No — AniList→provider→episode is correct |
| Release matching too strict | **Yes — this is the core gap** (see section 2) |
| Provider returns source but download transport fails | **No** — transport works (section 2 liveness) |
| ffprobe/post-download check rejects valid files | Only for genuinely mislabeled releases (correct) |
| Subtitles coupled to specific release/source | Nyaa fansub releases embed English soft-subs; extraction + mux works |

---

## 4. English subtitle availability by source

- **Nyaa (English-translated category, `c=1_2`):** every release in this
  category carries English soft-subs (that's the category's contract). The
  app extracts the embedded English track and muxes it into the mp4
  (`_find_sub_stream` → `mov_text`). Verified for the HorribleSubs Prison
  School batch: the file is an `.mkv` with an embedded subtitle track.
- **hianime:** English soft-subs (and sometimes Persian) are available in the
  source response, but the provider is IP-blocked from here.
- **Generated Persian:** `subtitle_translate` translates the embedded English
  track into Persian when the user asks for `fas`. This is a *derived* track,
  verified against the English track, not an independent source.

So English subtitles are **available from the working provider (Nyaa) for
every resolvable episode**. Subtitle availability is not the blocker.

---

## 5. Legitimate external sources/services — what's viable

Per the instruction to research *legitimate* sources (not "random scraping
sites"), I probed the two named candidates plus what the current providers
already give:

### OpenSubtitles API — viable as a separate subtitle layer
- `https://api.opensubtitles.com/api/v1` is a real, documented REST API.
  Discovery endpoints (`/infos/languages`) answer **keyless** (verified).
- Subtitle search (`/subtitles?query=…&season_number=&episode_number=`) and
  download require an **API key + login** (`X-Api-Key` + `/login` + JWT).
  Free tier exists (API key is free), but keys and per-key rate limits mean
  it's a **configured layer**, not a default keyless one — consistent with how
  the project treats hianime's keygen (env-overridable, not required).
- **Release verification is essential**: OpenSubtitles matches by title, not
  by file hash. The user's requirement — "verify release/title/episode
  compatibility before muxing" — means we should not blindly mux a subtitle
  onto an arbitrary release; we'd pair it by title+season+episode+language and
  prefer it only when the release has no embedded track. This is a reasonable
  Phase-2 subtitle provider behind the existing `subs=` plumbing.

### JustWatch — viable as an availability provider
- `apis.justwatch.com/graphql` is a **public GraphQL API, keyless**, verified
  live. `popularTitles(country:, language:)` returns offers with
  `offerCount(country:, platform:)` per title. It can tell which legitimate
  streaming services carry a show in a region.
- It is a **discovery/availability** layer, not a download source. It would
  let the UI say "this title is on Crunchyroll / Netflix in your region"
  (legitimate viewing), which complements (not replaces) the download path.
- Limitations: no search-by-title field exposed (I probed `searchTitles`,
  which exists but takes a `source` and returned empty), and introspection is
  disabled, so the schema has to be mapped empirically. Still, the core
  "which provider has this title in region X" query works keyless.

### No new "random scraping" providers
Per instruction, I am NOT adding animepahe/aniwatch/etc. as scrape sources.
They are ad-laden mirrors of the same blocked sites, often with inconsistent
quality labeling and ToS gray areas — outside the project's legitimate bar.

---

## 6. Recommended provider architecture

Keep the `providers.py` abstraction (the user's "Provider A / B / C /
Subtitle / Availability" shape), but let each provider **report its
capabilities** instead of every provider pretending it serves every quality:

```python
# conceptual — mirrors providers.EpisodeStream + adds capability
@dataclass
class ProviderCapability:
    provider: str
    episode: int
    qualities: list[str]        # ["480", "720", "1080"] — what this provider can actually serve
    subtitles: list[str]        # ["eng"] / ["eng", "fas"] — what tracks it can mux
    downloadable: bool
    status: str                 # "available" | "blocked" | "unavailable" (rot)
```

- **Nyaa** already returns per-quality resolution data via `_search_episode`.
  Add a `capabilities()` method that asks Nyaa which qualities actually have a
  seeded release, so the UI can list only those.
- **hianime** already has `available()` and an m3u8 master with variant
  heights; add a `capabilities()` that returns the master's heights and
  subtitle languages (and `status: "blocked"` when the site is unreachable).
- **New: OpenSubtitlesProvider** behind the existing `subs=` interface —
  discovers subtitles by title+season+episode+language, only when the release
  has no embedded track. Gated behind an API key.
- **New: JustWatchProvider (availability only)** — returns "which services
  carry this in your region", surfaced as a badge/link, never as a download
  source.

The provider chain (`providers()`, `_chain_excluding`) stays. A provider whose
`capabilities()` say a quality is unsupported is asked for a supported one or
skipped; the downloader still verifies served quality with ffprobe.

---

## 7. Frontend/backend changes for real provider-search progress

The user wants the search progress to reflect **actual backend progress**, not
a fake spinner. The job system already reports per-track stages; this adds a
provider-level progress event.

### Backend
1. **jobs.py**: extend `TrackState` with a provider-search progress field:
   ```python
   provider_progress: dict | None = None
   # {"phase": "resolving_source", "current_provider": "nyaa",
   #  "provider_index": 1, "provider_total": 2, "checked": 1, "remaining": 1}
   ```
   Included in `as_dict()`.
2. **anime/downloader.py**: `download_video_track` already walks
   `_chain_excluding` in order. Pass a callback `on_provider(provider,
   index, total)` that fills the dict at each provider attempt; on
   `ProviderError`/`QualityUnavailable`, set `current_provider` to the next
   one and bump `checked`.
3. **routes.py**: no change (the progress rides the existing job poll).
4. Add `capabilities()` to each provider (section 6) and expose it via a
   `GET /api/anime/{media_id}/season/{season}/sources` endpoint so the UI can
   list real available qualities before the user clicks download.

### Frontend
1. **DownloadsDock / AnimeSeasonView row**: when `status === 'searching'`
   and `track.provider_progress` is present, render
   `m.anime.searchingSources(provider_index, provider_total)` and a
   provider-name chip; when a provider fails, show
   `m.anime.sourceNoResult` (a friendly, non-technical string), never a raw
   exception.
2. **VideoQualityPicker**: instead of the static `['480','720','1080',
   'original']`, render the season's actual `capabilities().qualities`. If a
   requested quality is unavailable, the picker disables it (or the season
   view shows only the real ones). Never silently upgrade.
3. **Locales**: add `anime.searchingSources`, `anime.sourceNoResult`,
   `anime.sourceBlocked` to `en.ts` + `fa.ts` (typed as `Messages`).

---

## 8. Exact anime hero changes

Current (single-line, no accent):

```tsx
<h1 …>{m.anime.hero.title}</h1>            // 'دانلود انیمه با زیرنویس'
<p …>{m.anime.hero.description}</p>
```

Required (two lines, second line in the accent color, same typography as the
music hero):

```tsx
<h1 className="animate-fade-up font-display text-[clamp(2.5rem,7.5vw,4.5rem)] leading-[1.15] font-bold text-balance">
  {m.anime.hero.titleLine1}
  <br />
  <span className="text-lime-flash">{m.anime.hero.titleLine2}</span>
</h1>
<p className="mt-5 max-w-md animate-fade-up text-body leading-relaxed text-ink-300 [animation-delay:80ms]">
  {m.anime.hero.description}
</p>
```

Locales — replace `anime.hero.title` with:

- **fa.ts**
  ```ts
  hero: {
    titleLine1: 'دانلود انیمه،',
    titleLine2: 'با زیرنویس',
    description: 'انیمه‌ات رو جستجو کن، قسمت موردنظر و کیفیت دلخواهت رو انتخاب کن و فایل رو همراه با زیرنویس دریافت کن.',
  }
  ```
- **en.ts**
  ```ts
  hero: {
    titleLine1: 'Download anime,',
    titleLine2: 'with subtitles',
    description: 'Search for an anime, pick the episode and the quality you want, and get the file with subtitles.',
  }
  ```

The music hero is already tab-conditional in `App.tsx` (`{tab === 'anime' ? …
: <music hero>}`) and is never rendered on the anime tab — confirmed.

---

## 9. What I did NOT do (per instructions)

- Did **not** change any provider/downloader code.
- Did **not** add third-party scrape providers, force Consumet, or rewrite the
  downloader.
- Did **not** touch the music pipeline.
- Did **not** add a fake progress indicator.
- Did **not** delete anything on the VPS.

## Appendix — the exact data sources

All checks were run live against the deployed container. The probe scripts
(`src_cap_probe.py`, `src_cap_probe2.py`, `prison_trace.py`, `ps_swarm_test.py`,
`ps_rows.py`, `ps_filter_sim.py`, `quality_gap_sim.py`, `hi_probe.py`) are in
the investigation session. The Nyaa releases listed are the actual page
contents at probe time; seeders change but the *shape* of the catalog (which
releases carry episode numbers, which qualities exist) is stable.
