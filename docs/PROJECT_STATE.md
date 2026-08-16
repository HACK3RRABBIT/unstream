# Project State

The current, load-bearing state of Unstream: what it is, how it is wired,
what works, what was verified on real hardware, and what is next. Written so a
fresh `git clone` gives a new developer (or a new Claude session) everything a
conversation would have. Secrets never belong here — the disposable test VPS
is documented in [`TEST_VPS.md`](TEST_VPS.md), credentials kept out of Git
entirely.

## What Unstream is

A self-hosted music + anime downloader. Paste a **Spotify / Deezer / Apple
Music / YouTube / SoundCloud** URL, or search, and get tagged audio files — or
muxed anime episodes — at a chosen quality. No accounts, no API keys: every
metadata provider is public and keyless on purpose. The UI ships **Farsi
first** (default locale `fa`), English as a second locale, switchable from the
header.

Run it: `docker compose up -d` → <http://localhost:8080>. A full user guide is
in [`../README.md`](../README.md); the reasons behind the architecture are in
[`DESIGN.md`](DESIGN.md).

## Architecture

Two containers, same origin, no CORS:

- `backend/` — FastAPI (Python 3.12+, uv). Serves `/api` and the `/admin`
  dashboard. Code lives in `app/`.
- `frontend/` — Vite + React + Tailwind (Node 20+). nginx serves the built app
  **and** proxies `/api` to the backend over the internal network. In dev,
  Vite proxies `/api` → `:8000`.

All metadata providers map to the shapes in `app/models.py` (`Track`,
`Collection`, `SearchResult`); everything downstream sees only those shapes.

```
frontend (Vite + React + Tailwind) ──proxy /api──▶ backend (FastAPI)
                                                    ├─ embed.py       Spotify URL → metadata
                                                    ├─ deezer.py      search, artists, Deezer URLs
                                                    ├─ itunes.py      search, Apple Music URLs
                                                    ├─ soundcloud.py  search + SoundCloud URLs
                                                    ├─ ytdlp.py       YouTube/SoundCloud URLs + search
                                                    ├─ lyrics.py      lyrics, 3 sources, SQLite cache
                                                    ├─ downloader.py  find audio → encode → tags
                                                    ├─ jobs.py        thread pool + progress + sweeper
                                                    ├─ limits.py      per-caller budgets
                                                    ├─ analytics.py   SQLite counters + /admin
                                                    └─ anime/         AniList browse + episode downloads
```

### Backend structure

- `app/main.py` — routes: `/api/search`, `/api/resolve`, `/api/download`,
  `/api/jobs{/id}`, `/api/jobs/{id}/zip`, `/api/admin/*`, plus the anime router
  and `/config.js` for the locale.
- `app/downloader.py` — `download_track()` dispatches on `track.media`:
  `"video"` → the anime pipeline (imported lazily to avoid a cycle); `"audio"`
  → YouTube/SoundCloud via yt-dlp, mp3 encode at 128/192/320 kbps, or
  `original` (copy-codec remux, never a re-encode), then mutagen tags + cover
  + optional lyrics.
- `app/jobs.py` — in-memory jobs (no DB by design). `TrackState` per track
  (status, progress, `provider`, `served_quality`). Two pools: shared audio
  (`DOWNLOAD_WORKERS`, default 3) and a smaller video pool
  (`ANIME_DOWNLOAD_WORKERS`, default 2). `cancel()` sets a `threading.Event`
  and writes the terminal status; workers poll it between attempts and from
  inside the yt-dlp progress hook; `Cancelled` is its own exception so the
  retry loop never retries a cancellation. A sweeper thread evicts finished
  job dirs by TTL then by `MAX_DOWNLOADS_GB`.
- `app/lyrics.py` — LRCLIB → Genius → lyrics.ovh, with circuit breakers and an
  outage cache. **`None` = "sources answered, none has it"; `LyricsUnavailable`
  = "couldn't ask" — never collapse the two.** Genius is currently 403-blocked
  (per-address, kept for self-hosters).
- `app/analytics.py` — server-side SQLite counters, off unless `ADMIN_TOKEN`.
- `app/limits.py` — in-memory per-caller rate limits, off unless
  `RATE_LIMITS_ENABLED`.

### Frontend structure

- `frontend/src/App.tsx` — the app shell + routing.
- `frontend/src/lib/api.ts` — backend calls; `downloads.tsx` — the download
  dock state; `i18n.tsx` + `locales/{en,fa}.ts` — i18n (`en.ts` is the
  canonical shape, `fa.ts` must follow the `../CONTEXT.md` glossary);
  `analytics.ts`, `recent.ts`, `share.ts`, `preview.ts`, `toast.tsx`.
- `frontend/src/components/`, `frontend/src/admin/`.

## Anime pipeline

An episode is a `Track` with `media="video"` whose `source_url` is a synthetic
plan: `anime://<provider>/<animeId>/<season>/<episode>`. `routes.py`
(`/api/anime/*`) builds these from AniList metadata and hands the batch to
`jobs.start` — the same ZIP/cancel/sweeper path as music.

`anime/downloader.py#download_video_track()` walks the **provider chain**
(`PROVIDER_ORDER`, default `nyaa,hianime`): each provider's
`episode_stream()` is asked for the episode at the requested resolution; a
provider that fails is skipped and the next is asked at the **same** requested
quality. After the file exists, `_probe_height()` (ffprobe) gets the real
height and `_check_served_quality()` verifies it against the request before
the track can be marked done. `meta` records the real `provider` and
`served_quality` (from ffprobe, never echoed from the request); `jobs.py`
surfaces them as `TrackState.provider` / `TrackState.served_quality` in the
API.

### Providers

| Provider | Mode | Notes |
| -------- | ---- | ----- |
| **nyaa** | self-downloading (`streams_hls=False`) | Nyaa torrents via aria2c (or libtorrent). Works from any IP — the only anime source that does. First in the chain. |
| **hianime** | HLS (`streams_hls=True`) | yt-dlp with a strict format selector. Unreachable from datacenter IPs (bot-checked), so Nyaa is tried first. |

### Strict 480/720/1080/original quality behavior

- **Explicit resolution (480/720/1080):** only releases whose title clearly
  claims that resolution are eligible. If none exists, `QualityUnavailable` is
  raised — never silently serve another resolution.
- **`original`:** best-seeded torrent / best stream whatever the resolution.
- Nyaa's strict selector: `bestvideo[height=N]+bestaudio/best[height=N]` — no
  unrestricted `/best` fallback that could upgrade.
- **Post-download enforcement:** after the file exists, `_probe_height()` +
  `_check_served_quality()` verify the actual height. A release labeled 480p
  that is really 720p raises `QualityUnavailable` and the chain continues at
  the same requested quality. `served_quality` records the truth even when it
  differs from the request. `original` and the audio path are never checked.

### `QualityUnavailable`

Defined in `app/anime/providers.py`. Means "the episode exists, but not at the
requested resolution" — distinct from `ProviderError` (provider itself
unavailable). Lets the downloader try the next provider at the same quality;
if no provider can serve it, the job fails with
`Requested quality Xp is unavailable for this episode.`

**Error aggregation (`download_video_track`):** the clean quality message wins
whenever at least one provider raised `QualityUnavailable` — a missing release,
or a mislabeled one refused post-download — even if a later provider (HiAnime
rot / unreachable from the datacenter) fails with an unrelated `ProviderError`.
Only when no provider could evaluate the resolution do the real technical
errors survive, as `Failed to download episode after trying all providers: …`.
Observed live on the VPS: requesting 480p for Dandadan (only 1080p/2160p
released) now reports the quality message instead of being masked by HiAnime's
timeout.

### Nyaa resolution matching

`nyaa._title_resolution(title)` recognizes `480p`/`720p`/`1080p` markers and
`width×height` dimensions, with boundaries so `48000` (bitrate), `001-480`
(episode range), and bare numbers are **not** resolutions. An explicit request
filters candidates to those whose claimed resolution equals the request.

### Nyaa batch/range detection and extraction

`nyaa._parse_rows` classifies each search row as a **single** episode or a
**batch**:

- A row is a batch when its title has a multi-episode range covering the
  requested episode — separators `-`, `–`, `—`, `~` (any whitespace around
  them), episode at first/middle/last of the range (`_episode_ranges`) — or an
  explicit `[BATCH]` label, or a **space/comma-separated episode list**
  (`Show 001 002 003`, `Show E01 E02`) via `_multi_episode_space_list`.
- The space-list detector is deliberately **conservative**: only adjacent,
  standalone **zero-padded** episode forms (`01`/`001`/`010`) or `E`/`EP`
  markers count. A single episode beside a metadata number (`Show 01 720p`),
  a year (`2001`), or an `SxxExx` marker is never a batch — a missed batch
  falls through to the next provider, while a false positive would break a
  normal single-episode download, so it errs toward single.
- A multi-episode torrent is **never** whole-downloaded as if it were a single
  episode. Batches go through `_download_batch_episode`: the `.torrent` is
  fetched, aria2 `--show-files` / libtorrent file list is matched by
  `_file_is_episode`, and only the requested episode's file is downloaded.
- **Two aria2 gotchas, both fixed after being exposed live on the VPS:**
  1. aria2 1.37 emits each file as `idx|path` then `|length` on **two lines**
     (not `idx|path|length` on one) — the parser accepts a numeric-index line
     and ignores the length-only line.
  2. aria2 **preallocates every file** in a batch, so unselected files sit in
     the workdir as zero-filled look-alikes and "largest file" is wrong —
     the batch path returns the exact `--select-file`-ed file (verified to be
     a real file inside the workdir) instead of `_largest_video`.
- If the episode can't be extracted, the download fails cleanly so the chain
  can try the next provider.

### Persian subtitles

The release sources are English-subbed and the UI is Farsi-first, so Persian
subtitles are **generated by translating the English track** rather than waiting
for a Persian fansub that rarely exists. `subs` is now a **list** of requested
languages (`["eng"]` / `["fas"]` / `["eng","fas"]` / `[]`); the frontend exposes
English / فارسی / English + فارسی / None, defaulting to `["eng","fas"]`.

- **Acquisition differs per provider:** Nyaa embeds subtitles in the fansub
  `.mkv`; HiAnime offers an external file (usually VTT). Both are English
  sources. Nyaa extracts the embedded English to a temp SRT only when Persian
  is requested; HiAnime normalizes its downloaded file through the same stage.
- **Translation** (`anime/subtitle_translate.py`, `anime/subtitles.py`): SRT/VTT
  is parsed into cues whose timestamps are kept **verbatim**; only the dialogue
  text is translated via the existing keyless Google mechanism in
  `translate.py`. A `Translator` protocol is the seam for future LLM/keyed
  providers.
- **Cache** (`data/subtitle_translations.db`): keyed by `sha256(normalized
  English SRT) + target language` — a changed English subtitle re-translates; a
  repeat episode never does.
- **Muxing** muxes only what was requested (English-only keeps the legacy
  single-track path; `["fas"]` muxes Persian alone; `["eng","fas"]` muxes both).
  The original English stream is never replaced.
- **Failure semantics:** a translation failure never fails the download — the
  job completes with English, or a bare video if there's no English at all.

## Test status

**180 passed, 3 skipped** (183 total; the skips need ffmpeg/ffprobe on the
machine running the suite). Run:

```sh
cd backend && uv run pytest
```

The suite must stay network-independent. The anime tests stub the HTTP
boundary (`anilist.franchise`, `anilist._gql`, `nyaa._client.get`), stub the
provider registry (`providers()`), and feed canned Nyaa HTML. When a test
drives the video pipeline with a fake file, it stubs `_probe_height` (real
ffprobe returns `None` on non-video bytes, which fails the explicit-quality
check).

One dev-box caveat: if `all_proxy=socks://…` is exported (some desktop Linux
setups), `httpx.Client(...)` at module import in `nyaa.py` fails with "Unknown
scheme for proxy URL", failing every Nyaa test. That is the machine's proxy,
not the code — unset the proxy vars for the test run:

```sh
env -u all_proxy -u ALL_PROXY -u http_proxy -u HTTP_PROXY -u https_proxy \
    -u HTTPS_PROXY -u no_proxy -u NO_PROXY uv run pytest
```

## Local development

```sh
# Backend (Python 3.12+, uv, ffmpeg)
cd backend && uv sync && uv run uvicorn app.main:app --reload --port 8000

# Frontend (Node 20+)
cd frontend && npm install && npm run dev   # Vite at :5173, /api → :8000

# Locale: UNSTREAM_DEFAULT_LOCALE=en npm run dev starts in English
```

CI runs `uv sync --frozen && uv run pytest -q` (backend) and
`npm ci && npm run lint && npm run format:check && npm run build` (frontend).

## Environment / dependency notes

- **uv**-managed; `uv.lock` committed. `libtorrent` is a conditional dep
  (Python < 3.14) — aria2c is preferred at runtime when present.
- Key env vars (see `.env.example` for the full, authoritative list):
  `ANIME_PROVIDER_ORDER`, `ANIME_DOWNLOAD_WORKERS`, `DOWNLOAD_WORKERS`,
  `DOWNLOADS_TTL_HOURS`, `MAX_DOWNLOADS_GB`, `RATE_LIMITS_ENABLED`,
  `ADMIN_TOKEN`, `UNSTREAM_DEFAULT_LOCALE`, `PUID`/`PGID`.
- Source rot / IP reality: **Genius 403s** (kept — only Persian-script lyric
  source, block is per-address), **AniList 403s dev boxes**, **HiAnime blocked
  from datacenter IPs**, **YouTube needs home egress** (VPS →
  `LOGIN_REQUIRED`). Nyaa works from anywhere.

## Milestones (git history, newest first)

- `609f3e9` — **Enforce served quality post-download; fix Nyaa range
  detection**: `_check_served_quality` probes the finished file (ffprobe is the
  source of truth) and refuses an explicit 480/720/1080 request that wasn't
  actually served at that height; Nyaa `-`/`–`/`—`/`~` ranges and explicit
  `[BATCH]` labels route to single-episode extraction, never a whole-batch
  download. 13 hermetic tests.
- *(Changes this doc is written alongside — uncommitted at writing:*
  - *record `provider`/`served_quality` on a failed track — the pipeline fills
    `meta` before it can fail, and `jobs._run_track` copies it onto the job
    state in the error branch too, so the API reports requested-vs-served even
    when a download ends in `error`.*
  - *`QualityUnavailable` error aggregation — a resolution explicitly
    unavailable from any provider that could evaluate it now survives a later
    provider's unrelated `ProviderError`; the job fails with the clean
    "requested quality is unavailable" message instead of a generic
    provider/network error. Live-verified on the VPS (Dandadan 480p).*
  - *space/comma-separated episode lists (`001 002 003`, `E01 E02`) are now
    batches via `_multi_episode_space_list` — conservative: only adjacent
    zero-padded or E/EP-marked episode numbers count, so `Show 01 720p` stays
    a single. 4 hermetic tests.*
  - *aria2 batch-extraction fixes, both exposed live on the VPS (Montana Jones
    ep 2): `_download_batch_episode` now parses aria2 1.37's two-line
    `idx|path` / `|length` listing and returns the exact `--select-file`-ed
    file (aria2 preallocates unselected files, so "largest" would pick a
    zero-filled look-alike). 4 hermetic tests.*
  - *Persian subtitles — `subs` is a list of requested languages; the English
    track (Nyaa embedded or HiAnime external) is translated to Persian via the
    keyless Google mechanism, cached by content-hash, and muxed as requested
    tracks. 180 passed / 3 skipped; Docker-verified (real ffmpeg + real
    Google) for eng/fas/eng+fas; VPS confirmed the English subtitle stream.
    Also fixed two real bugs: ffmpeg `-map 0:s:N` per-type vs ffprobe global
    index, and language-tag matching for titled fansub streams (`2,eng,English`).*
- `b6fd892` — **Fix strict anime video quality selection**: `QualityUnavailable`,
  Nyaa resolution matching, strict yt-dlp selector, `served_quality` +
  `provider` tracking, 12 hermetic quality tests.
- `d5429f7` — Deploy: always build from source, not upstream prebuilt images.
- `15dd59f` — Deploy prep: dokploy anime vars + README torrent note.
- `009bf3b` — Anime: fix slow downloads, mobile settings, labels.
- `8263b9e` — Anime: drop 360p from quality options.
- `4fe1d71` — Anime: disambiguate seasons + batch extraction on Nyaa.
- `9c9fa69` — Add an anime section: AniList browse, Nyaa torrent downloads.
- Earlier: Spotify embed (`embed.py`), long-playlist paging, cancel, lyrics,
  original-codec remux — see `git log`.

## Verified on the disposable VPS

A temporary test VPS (see [`TEST_VPS.md`](TEST_VPS.md)) validates the
download core against the real internet — the one thing hermetic tests cannot
cover. Successful results:

- **Requested 480 → served 480 (One Piece ep 1100):** Nyaa selected
  `[SubsPlease] One Piece - 1100 (480p) [5880A6EB].mkv`; the final mp4 probed
  at **848×480** (h264, yuv420p, 23.976 fps, AAC 44.1 kHz stereo,
  ~1431 s); `served_quality="480p"`, `provider="nyaa"`, job `done`. No
  fallback, no error.
- The second run hit a **weaker swarm** (~180–230 KiB/s, 10–15 peers, 2
  seeds) and took ~1413 s instead of ~324 s — normal torrent variance, not a
  stall (progress confirmed continuously; aria2's own ETA tracked ~5 min).
  The on-disk file showed full size during download because aria2
  **preallocates**; real progress comes from aria2's summary log, which the
  test driver parses.
- **Requested 720 → served 720 (One Piece ep 1100, current code):** Nyaa
  selected `[SubsPlease] One Piece - 1100 (720p) [CC8AF482].mkv`; the final
  mp4 probed at **1280×720** (h264, yuv420p, 23.976 fps, AAC 44.1 kHz stereo,
  ~1431 s), 737 MB in ~246 s; `served_quality="720p"`, `provider="nyaa"`, job
  `done`. Post-download enforcement passed — served == requested.
- **Quality-unavailable aggregation (Dandadan S01E01 @ 480p):** Nyaa raised
  `QualityUnavailable` (only 1080p/2160p released) and HiAnime then timed out
  from the datacenter — the final job error is now the clean
  `Requested quality 480p is unavailable for this episode.` (before the
  aggregation fix it was masked by HiAnime's generic timeout).
- **Space-list batch extraction (Montana Jones - 01, 02, 03, Nyaa id 947397,
  requested episode 2):** the space/comma list was detected as `batch=True`,
  routed to `_download_batch_episode`, aria2 `--select-file=2` downloaded only
  episode 2, and the returned file was exactly episode 2 — 132,744,762 bytes
  (~126 MiB, matching the torrent listing), ffprobe **h264 640×480** (yuv420p,
  AAC, ~1509 s), `served_quality="480p"`, `provider="nyaa"`, job `done`. This
  is the live test that exposed and then verified both aria2 fixes (two-line
  `--show-files` + preallocation).
- **English subtitle mux (One Piece ep 1100 @ 720p, `subs=["eng"]`):** the final
  mp4 carries a **`subtitle,eng`** soft track — the real fansub stream was
  muxed correctly. This is the live run that exposed the two subtitle bugs
  (ffmpeg per-type vs ffprobe global sub index; titled `2,eng,English` language
  tags) and verified their fixes. **The Persian-only and eng+fas VPS runs were
  not completed: the BitTorrent swarm stalled on the final ~0.2% of the torrent
  across several attempts (One Piece and Tenmaku). This is a transient
  torrent/swarm availability limitation, NOT a confirmed application/code
  failure** — the identical code path was Docker-verified with real ffmpeg and
  real Google translation (eng / fas / eng+fas all produce the expected
  subtitle tracks).

## Known limitations

- A space/comma-separated episode list is now a batch when the numbers are
  **zero-padded or E/EP-marked** (`001 002 003`, `E01 E02`) — a deliberate
  false-negative remains for ambiguous forms (non-zero-padded `1 2 3`,
  `S01E01 S01E02`), which are still treated as a single rather than risk a
  false positive.
- Current Nyaa indexing had **no seeded `001 002 003` non-comma example** to
  live-test against (the `001 002 003` query returns nothing; the only such
  title, a German-sub Pocket Monsters, had 0 seeders) — the live batch test
  used the equivalent comma form `01, 02, 03`, which is the same detector path.
- `served_quality` records the truth but doesn't itself fail a mislabeled
  release — the post-download `_check_served_quality` does that; both exist.
- HiAnime never raises `QualityUnavailable` itself (its strictness relies on
  the yt-dlp selector erroring), but the downloader's error aggregation now
  preserves a `QualityUnavailable` from any provider that could evaluate the
  resolution, so the clean "quality unavailable" message fires even when
  HiAnime was the last provider to fail.
- AniList 403 on these dev boxes means the `/api/anime/download` route cannot
  build a season here; tests drive `jobs.start` with a constructed Track
  instead.
- Jobs are in-memory: a restart loses in-flight progress (accepted by design).
- Lyrics: Genius is blocked; Persian-script lyrics currently come only from
  LRCLIB/lyrics.ovh.

## Recommended next work / TODOs

1. ✅ **Post-download `served == requested` recorded on the job state even on
   failure** — **done**: the pipeline fills `meta` (provider + ffprobe'd
   height) before it can fail, and `jobs._run_track` copies it onto the
   `TrackState` in the error branch too. A mislabeled release that fails
   quality enforcement now reports `provider` / `served_quality` (the truth)
   alongside the error message. Hermetic test:
   `test_failed_track_still_records_served_quality`.
2. ✅ **Batch detection for non-range multi-episode titles** — **done**:
   space/comma-separated episode lists (`001 002 003`, `E01 E02`) are batches
   via `_multi_episode_space_list`, deliberately conservative (only adjacent
   zero-padded or E/EP-marked numbers) so `Show 01 720p` stays a single. 4
   hermetic tests. Live-verified on the VPS (Montana Jones ep 2, see the
   Verified section).
3. ✅ **HiAnime `QualityUnavailable`** — **done**: the provider-chain error
   aggregation in `download_video_track` now preserves a `QualityUnavailable`
   from any provider that could evaluate the resolution, so the final job
   error is the clean quality message even when HiAnime (rot / unreachable)
   was the last provider to fail. Live-verified on the VPS: Dandadan 480p now
   reports the quality message instead of HiAnime's timeout.
4. ✅ **Live-test the batch extraction path** on the VPS — **done**: Montana
   Jones `01, 02, 03` (episode 2, a middle episode of a real space-list batch)
   extracted only episode 2 end-to-end. This live run also exposed and fixed
   the two aria2 gotchas (two-line `--show-files` listing; preallocation
   making `_largest_video` pick a zero-filled file). 4 hermetic tests added.
5. ✅ **Persian subtitles** — **done**: `subs` is a list of requested languages
   (English / فارسی / English + فارسی / None); the English track (Nyaa embedded
   or HiAnime external) is translated to Persian via the keyless Google
   mechanism, cached by `sha256(normalized English SRT) + target`, and the
   requested tracks are muxed — a translation failure never fails the download.
   Backend **180 passed / 3 skipped**; frontend builds/lints/format clean;
   **Docker-verified** with real ffmpeg + real Google for eng / fas / eng+fas;
   VPS confirmed the English subtitle stream. Two real bugs were found and fixed
   during validation: ffmpeg `-map 0:s:N` **per-type** vs ffprobe **global**
   subtitle index, and language-tag matching for titled fansub streams
   (`2,eng,English`).
   **The VPS Persian-only and eng+fas runs were NOT completed — the BitTorrent
   swarm stalled on the final ~0.2% of the torrent across several attempts
   (transient swarm/availability, NOT a confirmed application/code failure); the
   Docker test covers the identical code path.**
6. ✅ **`docs/DESIGN.md` anime section** — **done**: the download pipeline,
   quality/error aggregation, batch extraction, and Persian-subtitle architecture
   are documented.
7. Anything a fresh clone must know that this conversation learned — keep
   updating this file rather than letting it live in chat.
