# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Unstream is a self-hosted music + anime downloader. Paste a Spotify / Deezer / Apple Music / YouTube / SoundCloud URL, or search, and get tagged audio files (or muxed anime episodes) at a chosen quality. No accounts, no API keys — every metadata provider is public and keyless by design. UI ships **Farsi-first**, English as a second locale. Two containers: FastAPI backend (`backend/`) and Vite + React frontend (`frontend/`); nginx serves the built frontend and proxies `/api` to the backend (same origin, no CORS). The live deployment is `compose.dokploy.yml` (GHCR images, rebuilt weekly); the one-command self-host flow is `docker-compose.yml`.

## Commands

Backend (Python 3.12+, [uv](https://docs.astral.sh/uv/)):
- Run the API: `cd backend && uv sync && uv run uvicorn app.main:app --reload --port 8000`
- Full tests: `cd backend && uv run pytest`. One file: `uv run pytest tests/test_anime.py -q`. One test: `uv run pytest tests/test_anime.py::test_nyaa_range_separators_are_batches -q`

Frontend (Node 20+):
- Dev server: `cd frontend && npm install && npm run dev` (Vite at :5173, proxies `/api` → :8000)
- `npm run lint` (oxlint) · `npm run format:check` (prettier) · `npm run build` (runs `tsc -b`, then Vite)

CI runs `uv sync --frozen && uv run pytest -q` for the backend and `npm ci && npm run lint && npm run format:check && npm run build` for the frontend. `UNSTREAM_DEFAULT_LOCALE` (fa|en) is read into a `/config.js` the frontend loads at startup, so it works in dev too: `UNSTREAM_DEFAULT_LOCALE=en npm run dev`.

## Architecture — read these together

Everything downstream sees only the shapes in `app/models.py` (`Track`, `Collection`, `SearchResult`); every metadata provider maps to them. `Track.media` is `"audio"` (music pipeline) or `"video"` (anime episodes), and `Track.source_url` either points at a real YouTube/SoundCloud page or carries a synthetic `anime://<provider>/<animeId>/<season>/<episode>` plan.

Metadata providers (`embed.py` Spotify embed pages, `deezer.py`, `itunes.py`, `soundcloud.py`, `ytdlp.py`) are all keyless; search fans out to several in parallel and dedupes by name + artist. There is deliberately **no Spotify Web API** (requires a Premium subscription since 2025).

**Download pipeline** (`app/downloader.py`): `download_track()` dispatches on `track.media`; for video it lazily imports the anime pipeline (avoids a cycle). For audio: download the track's own URL, or `ytsearch8: "<artists> - <title>"` picking the result whose duration is closest to the catalog's (rejects live versions / hour-long mixes); retries exclude the exact uploads that failed, and the last attempt searches SoundCloud instead of YouTube. `quality` is an mp3 bitrate (`128`/`192`/`320`) or `original` — the latter keeps the upload's own stream, copy-codec remuxed into a pure-audio container (webm→opus, combined mp4→m4a). mutagen tags in whatever format the container speaks; cover art and lyrics are nice-to-have and never fail a download.

**Jobs** (`app/jobs.py`): in-memory only — a restart loses in-flight progress (accepted trade; the files on disk are the durable artifact, and a queue would be a second stateful service). One `TrackState` per track carries status/progress and, for anime, `provider` + `served_quality`. Two thread pools: the shared audio pool (`DOWNLOAD_WORKERS`, default 3) and a smaller video pool (`ANIME_DOWNLOAD_WORKERS`, default 2) because episodes are 100–700 MB each. Cancellation is `threading.Event` set by `cancel()` (which also writes the terminal status so the UI answers instantly); workers poll it between attempts and from inside the yt-dlp progress hook, and `Cancelled` is its own exception so the retry loop never mistakes being called off for a broken upload. A background sweeper evicts finished job dirs by TTL then by `MAX_DOWNLOADS_GB`.

**Anime** (`app/anime/`):
- `providers.py` — `EpisodeSource` / `EpisodeStream`, `QualityUnavailable`, and the provider registry; order from `ANIME_PROVIDER_ORDER` env (default `nyaa,hianime`).
- `routes.py` (`/api/anime/*`) — AniList search/browse, and a download endpoint that resolves a season to episode `Track`s and hands the batch to `jobs.start` — the same ZIP/cancel/sweeper path as music.
- `nyaa.py` — self-downloading provider (`streams_hls=False`). Searches nyaa.si by title+episode, classifies rows into singles vs batches (`_episode_ranges` / `_RANGE_RE` / `_BATCH_TAG_RE`), matches requested resolution strictly (`_title_resolution`; raises `QualityUnavailable` when no release claims it), torrents via aria2c or libtorrent, and extracts a single episode out of multi-episode batches. **A multi-episode torrent must never be whole-downloaded as if it were a single episode** — if the episode can't be extracted it fails cleanly so the chain can try the next provider.
- `hianime.py` — HLS scraper (`streams_hls=True`); yt-dlp with a strict `_format_selector` (exact height, no unrestricted `/best`).
- `anime/downloader.py` — `download_video_track()` walks the provider chain (a failing provider is skipped; the next is asked at the **same** requested quality), then verifies the result before success: `_probe_height` (ffprobe, the source of truth) and `_check_served_quality` — an explicit 480/720/1080 that isn't actually served at that height (or can't be verified) raises `QualityUnavailable` and the chain continues. `meta` records the real `provider` + `served_quality` (never echoed from the request), which jobs.py surfaces as `TrackState.provider` / `served_quality` in the API.
- `anilist.py` (metadata) + `translate.py` (keyless Google-Translate of synopses, SQLite-cached).

Quality is two unrelated concepts: audio **bitrate** and video **resolution**. `DEFAULT_QUALITY` / `DEFAULT_VIDEO_QUALITY` handle them separately.

Lyrics (`app/lyrics.py`): LRCLIB → Genius → lyrics.ovh, with circuit breakers that rest a refusing source and an outage cache (a hit or a miss is served from cache; a cached outage is bypassed by `refresh=1`). **`None` means "sources answered, none has it"; `LyricsUnavailable` means "couldn't ask" — never collapse the two** (collapsing them hid the Genius outage from the metrics for months). Analytics (`app/analytics.py`) is server-side SQLite, off unless `ADMIN_TOKEN` is set.

## Load-bearing decisions (details in `docs/DESIGN.md`, `README.md`)

- **Keyless-only is the constraint that drives provider choice.** That's why the app reads Spotify embed pages instead of the API, and why the lyrics/translate layers are the fragile, rot-prone ones.
- **Sources rot and are IP-dependent:** Genius 403s right now (kept — it's the only Persian-script lyric source, and the block looks per-address, which is fine for self-hosters), AniList 403s these dev boxes, HiAnime is unreachable from datacenter IPs, and YouTube answers `LOGIN_REQUIRED` from a VPS (home egress required). Nyaa is the only anime provider that works from anywhere — it is first in the chain for that reason. A source that starts refusing is rested, not removed.
- Don't add a Spotify Web API integration, and don't rebuild a transliterator for LRCLIB — both were tried and rejected (design notes explain why).
- **Jobs live in memory by design.** Don't add a queue or database for them.

## Testing

The entire backend suite is hermetic — no network. The pattern (see `tests/test_anime.py`): stub the HTTP boundary (`anilist.franchise`, `anilist._gql`, `nyaa._client.get`), stub the provider registry via `providers()`, and feed canned Nyaa HTML as fixtures. When a test drives the video pipeline with a fake file, stub `_probe_height` too — the real ffprobe returns `None` on non-video bytes, which fails the explicit-quality check. Tests use `monkeypatch` and in-process fixtures only.

## Git

Commits go straight to `main` (no feature branches). Don't push unless asked.
