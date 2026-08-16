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
  explicit `[BATCH]` label.
- A multi-episode torrent is **never** whole-downloaded as if it were a single
  episode. Batches go through `_download_batch_episode`: the `.torrent` is
  fetched, aria2 `--show-files` / libtorrent file list is matched by
  `_file_is_episode`, and only the requested episode's file is downloaded.
- If the episode can't be extracted, the download fails cleanly so the chain
  can try the next provider.

## Test status

**154 passed** (full backend suite, hermetic — no network). Run:

```sh
cd backend && uv run pytest
```

The suite must stay network-independent. The anime tests stub the HTTP
boundary (`anilist.franchise`, `anilist._gql`, `nyaa._client.get`), stub the
provider registry (`providers()`), and feed canned Nyaa HTML. When a test
drives the video pipeline with a fake file, it stubs `_probe_height` (real
ffprobe returns `None` on non-video bytes, which fails the explicit-quality
check).

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

*(The next commit, when landed: **Enforce served quality post-download; fix
Nyaa range detection** — this doc is written alongside it.)*

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

## Known limitations

- A multi-episode torrent with **no** range and **no** `[BATCH]` label (e.g.
  space-separated `001 002 003`) is still treated as a single.
- `served_quality` records the truth but doesn't itself fail a mislabeled
  release — the post-download `_check_served_quality` does that; both exist.
- HiAnime never raises `QualityUnavailable` (its strictness relies on the
  yt-dlp selector erroring), so the clean "quality unavailable" message only
  fires when the last provider raised it.
- AniList 403 on these dev boxes means the `/api/anime/download` route cannot
  build a season here; tests drive `jobs.start` with a constructed Track
  instead.
- Jobs are in-memory: a restart loses in-flight progress (accepted by design).
- Lyrics: Genius is blocked; Persian-script lyrics currently come only from
  LRCLIB/lyrics.ovh.

## Recommended next work / TODOs

1. **Post-download `served == requested` recorded on the job state even on
   failure** — today a mismatch falls through and only the final success (or
   the clean error message) is reported.
2. **Batch detection for non-range multi-episode titles** (space-separated
   episode lists, `[Batch]` already handled).
3. **HiAnime `QualityUnavailable`** — surface its missing-resolution case with
   the same message so the last-provider error is always the quality message.
4. **Live-test the batch extraction path** on the VPS (request an episode that
   only exists inside a batch) — covered by hermetic tests only so far.
5. **`docs/DESIGN.md`** could gain a section on the anime quality/batch
   decisions (currently only the README and this doc cover them).
6. Anything a fresh clone must know that this conversation learned — keep
   updating this file rather than letting it live in chat.
